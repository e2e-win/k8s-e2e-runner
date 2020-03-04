variable rg_name {}
variable location {}
variable rg_build_id {}
variable rg_creation_timestamp {}
variable rg_job_name {}
variable master_vm_name {}
variable master_vm_size {}
variable win_minion_count {}
variable win_minion_vm_size {}
variable win_minion_vm_name_prefix {}
variable win_minion_vm_username {}
variable win_minion_vm_password {}
variable ssh_key_data {}
variable win_img_publisher {}
variable win_img_offer {}
variable win_img_sku {}
variable win_img_version {}

variable azure_sub_id {}
variable "azure_client_id" {}
variable "azure_client_secret" {}
variable "azure_tenant_id" {}

# Configure the Microsoft Azure Provider
provider "azurerm" {
  subscription_id = "${var.azure_sub_id}"
  client_id       = "${var.azure_client_id}"
  client_secret   = "${var.azure_client_secret}"
  tenant_id       = "${var.azure_tenant_id}"
}

# Create a resource group if it doesn’t exist
resource "azurerm_resource_group" "clusterRg" {
  name     = "${var.rg_name}"
  location = "${var.location}"
  tags = {
    buildID = "${var.rg_build_id}"
    creationTimestamp = "${var.rg_creation_timestamp}"
    jobName = "${var.rg_job_name}"
  }
}

# Create virtual network
resource "azurerm_virtual_network" "clusterNet" {
  name                = "clusterNet"
  address_space       = ["192.168.0.0/16"]
  location            = "${var.location}"
  resource_group_name = "${azurerm_resource_group.clusterRg.name}"
}

# Create routeTable

resource "azurerm_route_table" "routeTable" {
  name                = "routeTable"
  resource_group_name = "${azurerm_resource_group.clusterRg.name}"
  location            = "${var.location}"
}

# Create subnet
resource "azurerm_subnet" "clusterSubnet" {
  name                 = "clusterSubnet"
  resource_group_name  = "${azurerm_resource_group.clusterRg.name}"
  virtual_network_name = "${azurerm_virtual_network.clusterNet.name}"
  address_prefix       = "192.168.168.0/24"
  route_table_id       = "${azurerm_route_table.routeTable.id}"
  depends_on           = ["azurerm_route_table.routeTable"]
}

# Master VM Definition
# Create public IPs
resource "azurerm_public_ip" "masterPublicIP" {
  name                = "masterPublicIP"
  location            = "${var.location}"
  resource_group_name = "${azurerm_resource_group.clusterRg.name}"
  allocation_method   = "Dynamic"
}

# Create Network Security Group and rule
resource "azurerm_network_security_group" "masterNSG" {
  name                = "masterNSG"
  location            = "${var.location}"
  resource_group_name = "${azurerm_resource_group.clusterRg.name}"

  security_rule {
    name                       = "SSH"
    priority                   = 1001
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "SSL"
    priority                   = 1002
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "443"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

# Create network interface
resource "azurerm_network_interface" "masterNic" {
  name                      = "masterNic"
  location                  = "${var.location}"
  resource_group_name       = "${azurerm_resource_group.clusterRg.name}"
  network_security_group_id = "${azurerm_network_security_group.masterNSG.id}"
  enable_ip_forwarding      = true

  ip_configuration {
    name                          = "masterNicConfig"
    subnet_id                     = "${azurerm_subnet.clusterSubnet.id}"
    private_ip_address_allocation = "Dynamic"
    public_ip_address_id          = "${azurerm_public_ip.masterPublicIP.id}"
  }
}

# Create virtual machine
resource "azurerm_virtual_machine" "masterVM" {
  name                  = "${var.master_vm_name}"
  location              = "${var.location}"
  resource_group_name   = "${azurerm_resource_group.clusterRg.name}"
  network_interface_ids = ["${azurerm_network_interface.masterNic.id}"]
  vm_size               = "${var.master_vm_size}"

  storage_os_disk {
    name              = "myOsDisk"
    caching           = "ReadWrite"
    create_option     = "FromImage"
    managed_disk_type = "Premium_LRS"
    disk_size_gb      = "100"
  }

  storage_image_reference {
    publisher = "Canonical"
    offer     = "UbuntuServer"
    sku       = "18.04-LTS"
    version   = "latest"
  }

  os_profile {
    computer_name  = "${var.master_vm_name}"
    admin_username = "ubuntu"
  }

  os_profile_linux_config {
    disable_password_authentication = true

    ssh_keys {
      path     = "/home/ubuntu/.ssh/authorized_keys"
      key_data = "${var.ssh_key_data}"
    }
  }
}

# Minion Windows VM Config
###### 

# Create Network Security Group and rule
resource "azurerm_network_security_group" "winNSG" {
  name                = "winMinionNSG"
  location            = "${var.location}"
  resource_group_name = "${azurerm_resource_group.clusterRg.name}"

  security_rule {
    name                       = "SSH"
    priority                   = 1001
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "22"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }

  security_rule {
    name                       = "WinRM"
    priority                   = 1003
    direction                  = "Inbound"
    access                     = "Allow"
    protocol                   = "Tcp"
    source_port_range          = "*"
    destination_port_range     = "5986"
    source_address_prefix      = "*"
    destination_address_prefix = "*"
  }
}

# Create network interface
resource "azurerm_network_interface" "winMinNic" {
  count                     = "${var.win_minion_count}"
  name                      = "winMinNic-${count.index}"
  location                  = "${var.location}"
  resource_group_name       = "${azurerm_resource_group.clusterRg.name}"
  network_security_group_id = "${azurerm_network_security_group.winNSG.id}"
  enable_ip_forwarding      = true

  ip_configuration {
    name                          = "winMinNicConfig-${count.index}"
    subnet_id                     = "${azurerm_subnet.clusterSubnet.id}"
    private_ip_address_allocation = "Dynamic"
  }
}

resource "azurerm_virtual_machine_extension" "powershell_winservices" {
  count                = "${var.win_minion_count}"
  name                 = "EnableWinServices"
  location             = "${var.location}"
  resource_group_name  = "${azurerm_resource_group.clusterRg.name}"
  virtual_machine_name = "${element(azurerm_virtual_machine.winMinionVM.*.name, count.index)}"
  publisher            = "Microsoft.Compute"
  type                 = "CustomScriptExtension"
  type_handler_version = "1.7"

  settings = <<SETTINGS
    {
        "fileUris": ["https://raw.githubusercontent.com/e2e-win/k8s-e2e-runner/master/v2/enableWinServices.ps1"],
        "commandToExecute": "powershell -ExecutionPolicy Unrestricted -File enableWinServices.ps1 *> C:\\enableWinServices.log"
    }
SETTINGS
}

# Create virtual machine
resource "azurerm_virtual_machine" "winMinionVM" {
  count                 = "${var.win_minion_count}"
  name                  = "${var.win_minion_vm_name_prefix}${count.index}"
  location              = "${var.location}"
  resource_group_name   = "${azurerm_resource_group.clusterRg.name}"
  network_interface_ids = ["${element(azurerm_network_interface.winMinNic.*.id, count.index)}"]
  vm_size               = "${var.win_minion_vm_size}"

  storage_os_disk {
    name              = "WinOSDisk-${count.index}"
    caching           = "ReadWrite"
    create_option     = "FromImage"
    managed_disk_type = "Premium_LRS"
    disk_size_gb      = "100"
  }

  storage_image_reference {
    publisher = "${var.win_img_publisher}"
    offer     = "${var.win_img_offer}"
    sku       = "${var.win_img_sku}"
    version   = "${var.win_img_version}"
  }

  os_profile {
    computer_name  = "${var.win_minion_vm_name_prefix}${count.index}"
    admin_username = "${var.win_minion_vm_username}"
    admin_password = "${var.win_minion_vm_password}"
  }

  os_profile_windows_config {
    provision_vm_agent = true
  }
}

data "azurerm_public_ip" "masterPublicIP" {
  depends_on          = ["azurerm_virtual_machine.masterVM"]
  name                = "${azurerm_public_ip.masterPublicIP.name}"
  resource_group_name = "${azurerm_resource_group.clusterRg.name}"
}

output "master" {
  value = "${map(azurerm_virtual_machine.masterVM.name, data.azurerm_public_ip.masterPublicIP.ip_address)}"
}

output "privateips" {
  value = "${zipmap(azurerm_virtual_machine.winMinionVM.*.name, azurerm_network_interface.winMinNic.*.private_ip_address)}"
  depends_on = ["azurerm_network_interface.winMinNic"]
}
