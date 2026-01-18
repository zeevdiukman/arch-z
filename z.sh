#!/bin/bash

# set -e
read -r -p "Seed device(/dev/vda1): " seed_device
read -r -p "Sprout device(/dev/vda2): " sprout_device
read -r -p "EFI device(/dev/vda3): " efi_device
if [[ -z "$seed_device" ]]; then
    seed_device="vda1"
fi
if [[ -z "$sprout_device" ]]; then
    sprout_device="vda2"
fi
if [[ -z "$efi_device" ]]; then
    efi_device="vda3"
fi
echo "Seed device: /dev/$seed_device"
echo "Sprout device: /dev/$sprout_device"
echo "EFI device: /dev/$efi_device"
read -r -p "Are you sure? (y,N): " response
if [[ "$response" != "y" && "$response" != "Y" ]]; then
    echo "Aborting."
    exit 1
fi
mkfs.btrfs -f -L SEED /dev/$seed_device
mkfs.btrfs -f -L SPRUT /dev/$sprout_device
mkfs.fat -F 32 -n EFI /dev/$efi_device
echo "Filesystems created successfully."
mount -o subvol=/ /dev/vda1 /mnt
btrfs su cr /mnt/@
umount -R /mnt
mount -o subvol=/@ /dev/vda1 /mnt
# ask user to input packages to install
read -r -p "Enter packages to install (space-separated): " packages_input
packages=($packages_input)
# ask user for package installation confirmation
echo "The following packages will be installed: ${packages[@]}"
# ask user for confirmation before proceeding
read -r -p "Continue with installation? (y,N): " response
if [[ "$response" != "y" && "$response" != "Y" ]]; then
    echo "Installation aborted."
    exit 1
fi
pacstrap -K /mnt ${packages[@]}
mount -m /dev/vda3 /mnt/efi
genfstab -U /mnt > /mnt/etc/fstab
arch-chroot /mnt