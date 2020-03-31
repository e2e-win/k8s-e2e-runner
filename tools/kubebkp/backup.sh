#!/bin/bash

log_msg() {
	echo "$(date -R) - $1"
}

log_msg "Starting backup..."

# Create backup folders
BACKUP_DIR=$(mktemp -d)
mkdir -p "${BACKUP_DIR}/configmaps"
mkdir -p "${BACKUP_DIR}/secrets"

# Backup configmaps
if [ -z "${BACKUP_CONFIGMAPS}" ]; then
	log_msg "BACKUP_CONFIGMAPS is empty. Not backing up any configmaps."
else
	for configmap in ${BACKUP_CONFIGMAPS//,/ }; do
		log_msg "Backing up configmap '${configmap}'..."
		kubectl get configmap "${configmap}" -o yaml > "${BACKUP_DIR}/configmaps/${configmap}.yaml"
	done
fi

# Backup secrets
if [ -z "${BACKUP_SECRETS}" ]; then
	log_msg "BACKUP_SECRETS is empty. Not backing up any secrets."
else
	for secret in ${BACKUP_SECRETS//,/ }; do
		SECRET_NAMESPACE=$(echo "${secret}" | cut -d/ -f1)
		SECRET_NAME=$(echo "${secret}" | cut -d/ -f2)
		log_msg "Backing up secret '${SECRET_NAME}' from namespace '${SECRET_NAMESPACE}'..."
		kubectl get secret "${SECRET_NAME}" -n "${SECRET_NAMESPACE}" -o yaml > "${BACKUP_DIR}/secrets/${SECRET_NAMESPACE}_${SECRET_NAME}.yaml"
	done
fi

# Create backup archive
ARCHIVE_NAME="backup-$(date +%Y-%m-%d_%H-%M)"
log_msg "Creating archive '${ARCHIVE_NAME}.tar.gz'..."
tar -Pzcf "/tmp/${ARCHIVE_NAME}.tar.gz" "${BACKUP_DIR}"
rm -rf "${BACKUP_DIR}"

# Encrypt backup archive
if [ -z "${ENCRYPTION_KEY}" ]; then
	log_msg "ENCRYPTION_KEY is empty. Skipping archive encryption and upload..."
	exit
else
	log_msg "Encrypting archive /tmp/${ARCHIVE_NAME}.tar.gz..."
	# Create key
	openssl rand -out "/tmp/${ARCHIVE_NAME}.key" 32
	# Encrypt archive
	openssl enc -in "/tmp/${ARCHIVE_NAME}.tar.gz" -out "/tmp/${ARCHIVE_NAME}.tar.gz.enc" -pass file:"/tmp/${ARCHIVE_NAME}.key"

	# Encrypt key using public ssh key
	openssl rsautl -encrypt -pubin -inkey <(ssh-keygen -e -f "${ENCRYPTION_KEY}" -m PKCS8) \
		-in "/tmp/${ARCHIVE_NAME}.key" -out "/tmp/${ARCHIVE_NAME}.key.enc"

	rm "/tmp/${ARCHIVE_NAME}.tar.gz"
	rm "/tmp/${ARCHIVE_NAME}.key"
fi

# Trim whitespace from env vars
AZURE_STORAGE_ACCOUNT="${AZURE_STORAGE_ACCOUNT//[[:space:]]/}"
AZURE_STORAGE_ACCOUNT_KEY="${AZURE_STORAGE_ACCOUNT_KEY//[[:space:]]/}"
AZURE_STORAGE_CONTAINER_PROW_BKP="${AZURE_STORAGE_CONTAINER_PROW_BKP//[[:space:]]/}"

# Upload backup archive
for file in "${ARCHIVE_NAME}.tar.gz.enc" "${ARCHIVE_NAME}.key.enc"; do
	log_msg "Uploading file ${file}..."
	az storage blob upload --no-progress --container-name "${AZURE_STORAGE_CONTAINER_PROW_BKP}" \
		--account-key "${AZURE_STORAGE_ACCOUNT_KEY}" --file "/tmp/${file}" --name "${file}"
done

rm "/tmp/${ARCHIVE_NAME}.key.enc"
rm "/tmp/${ARCHIVE_NAME}.tar.gz.enc"

# Cleanup old blobs
ALL_BLOBS=$(az storage blob list --container-name "${AZURE_STORAGE_CONTAINER_PROW_BKP}" --account-key "${AZURE_STORAGE_ACCOUNT_KEY}")
for blob in $(echo "${ALL_BLOBS}" | jq -r '.[] | .name + "," + .properties.lastModified'); do
	BLOB_FILE=$(echo "${blob}" | cut -d, -f1)
	BLOB_DATE=$(echo "${blob}" | cut -d, -f2)
	BLOB_AGE=$(dateutils.ddiff -f "%d" "${BLOB_DATE}" now)

	if [ -n "${BACKUP_KEEP_DAYS}" ] && [ "${BLOB_AGE}" -gt "${BACKUP_KEEP_DAYS}" ]; then
		logmsg "Deleting blob ${BLOB_FILE} (age: ${BLOB_AGE}D)"
		az storage blob delete --container-name "${AZURE_STORAGE_CONTAINER_PROW_BKP}" \
			--account-key "${AZURE_STORAGE_ACCOUNT_KEY}" --name "${BLOB_FILE}"
	fi
done
