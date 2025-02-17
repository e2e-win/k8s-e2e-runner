# Prow on AKS cluster

## Deploy AKS

```bash
az aks create \
    --resource-group k8s-sig-windows-networking \
    --name sig-win-networking-prow \
    --node-count 2 \
    --vm-set-type VirtualMachineScaleSets \
    --node-vm-size Standard_D2_v4 \
    --node-osdisk-size 256 \
    --kubernetes-version 1.29.11 \
    --tags "DO-NOT-DELETE=contact SIG-Windows" \
    --yes
```

## Install Prow

The [Deploying Prow](https://docs.prow.k8s.io/docs/getting-started-deploy) guide is used, as a reference, for this.

### Create namespaces

```bash
kubectl create namespace prow
kubectl create namespace test-pods
```

### Create required Prow config files

This is achieved by running the following `make` targets from [here](https://github.com/e2e-win/k8s-e2e-runner/blob/main/prow/Makefile):

```bash
make update-prow-config
make update-prow-plugins
make update-prow-job-config
```

### Restore Prow from backup

Fetch the latest Prow encrypted backup archive. This assumes that:

* Access to the Prow backup storage account is configured.
* The Prow private SSH key is available.

For example, let's assume that I want to restore the backup from `2023-December-04`, and the Prow SSH private key is stored at `$HOME/.ssh/k8s_win_ci/prow_k8s_win_id_rsa`:

```bash
BACKUP_STORAGE_CONTAINER="flannel-prow-backup"
BACKUP_NAME="backup-2023-12-04_00-00"
DECRYPTION_FILE="$HOME/.ssh/k8s_win_ci/prow_k8s_win_id_rsa"

# Download encrypted backup archive from Azure storage account (assuming "az" is installed, and access to the storage account is already configured)
az storage blob download -c $BACKUP_STORAGE_CONTAINER -n ${BACKUP_NAME}.key.enc -f ${BACKUP_NAME}.key.enc -o table
az storage blob download -c $BACKUP_STORAGE_CONTAINER -n ${BACKUP_NAME}.tar.gz.enc -f ${BACKUP_NAME}.tar.gz.enc -o table

# Decrypt the backup symmetric key using the private SSH key
openssl rsautl -decrypt -oaep \
    -inkey ${DECRYPTION_FILE} \
    -in ${BACKUP_NAME}.key.enc -out ${BACKUP_NAME}.key

# Decrypt the backup archive using the symmetric key
openssl enc -d -aes-256-cbc -md sha512 -pbkdf2 \
    -in ${BACKUP_NAME}.tar.gz.enc -out ${BACKUP_NAME}.tar.gz \
    -pass file:${BACKUP_NAME}.key

# Extract backup archive
tar -xzvf ${BACKUP_NAME}.tar.gz
```

### Deploy Prow CRDs

```bash
kubectl apply --server-side=true -f https://raw.githubusercontent.com/kubernetes/test-infra/master/config/prow/cluster/prowjob-crd/prowjob_customresourcedefinition.yaml
```

### Install Prow cluster

```bash
kubectl apply -f https://raw.githubusercontent.com/e2e-win/k8s-e2e-runner/main/prow/cluster.yaml
```

### Prow post-installation steps

#### Configure Traefik ingress controller

Follow the instructions from [prow/ingress/traefik](https://github.com/e2e-win/k8s-e2e-runner/tree/main/prow/ingress/traefik) for this.

#### Configure the GitHub repository managed by Prow

In the [e2e-win/k8s-e2e-runner](https://github.com/e2e-win/k8s-e2e-runner) GitHub repository managed by Prow, we need to do the following:

* Add the wehook to the repository (instruction [here](https://docs.prow.k8s.io/docs/getting-started-deploy/#add-the-webhook-to-github)).
* Install the Prow GitHub application (instruction [here](https://docs.prow.k8s.io/docs/getting-started-deploy/#install-prow-for-a-github-organization-or-repo)).
