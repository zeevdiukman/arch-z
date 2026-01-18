#!/bin/bash

set -e

# Configuration variables (will be populated via user selection)
seed_device=""
sprout_device=""
efi_device=""

# 1. Select Disk
echo "Available storage disks:"
mapfile -t disks < <(lsblk -p -dno NAME,SIZE,MODEL)
if [ ${#disks[@]} -eq 0 ]; then
    echo "No disks found!"
    exit 1
fi

PS3="Select a disk to choose partitions from (default 1): "
select disk_info in "${disks[@]}"; do
    if [[ -z "$REPLY" ]]; then
        disk_info="${disks[0]}"
    fi
    if [[ -n "$disk_info" ]]; then
        selected_disk=$(echo "$disk_info" | awk '{print $1}')
        break
    else
        echo "Invalid selection."
    fi
done


# 2. Select Partitions
get_partition() {
    local prompt="$1"
    local ps3_val="$2"
    local var_name="$3"
    local default_val="$4"
    local parts=()
    while IFS= read -r line; do
        parts+=("$line")
    done < <(lsblk -p -nlo NAME,SIZE,TYPE "$selected_disk" | awk '$3=="part" {printf "%s (%s)\n", $1, $2}')

    if [ ${#parts[@]} -eq 0 ]; then
        echo "No partitions found on $selected_disk!"
        exit 1
    fi

    echo ""
    echo "$prompt"
    PS3="$ps3_val (default 1): "
    local selection
    select selection in "${parts[@]}"; do
        if [[ -z "$REPLY" ]]; then
            selection="${parts[0]}"
        fi
        if [[ -n "$selection" ]]; then
            eval "$var_name=$(echo "$selection" | cut -d' ' -f1)"
            break
        else
            echo "Invalid selection."
        fi
    done
}

get_partition "--- Select Seed Partition ---" "Seed device: " seed_device
get_partition "--- Select Sprout Partition ---" "Sprout device: " sprout_device
get_partition "--- Select EFI Partition ---" "EFI device: " efi_device

echo ""
echo "Configuration Summary:"
echo "Seed device:   $seed_device"
echo "Sprout device: $sprout_device"
echo "EFI device:    $efi_device"
echo ""

read -r -p "Confirm formatting and installation? (yes/no/skip): " response
if [[ "$response" != "yes" && "$response" != "y" && "$response" != "Y" && "$response" != "skip" ]]; then
    echo "Aborting."
    exit 1
fi
if [[ "$response" == "skip" ]]; then
    echo "Skipping formatting"
else
    mkfs.btrfs -f -L SEED "$seed_device"
    mkfs.btrfs -f -L SPRUT "$sprout_device"
    mkfs.fat -F 32 -n EFI "$efi_device"
    echo "Filesystems created successfully."
fi
if mountpoint -q /mnt; then
    echo "/mnt is already mounted. Unmounting..."
    umount -R /mnt
fi

mount -o subvol=/ "$seed_device" /mnt
# if subvolume @ exists remove and create new one
if btrfs subvolume list /mnt | grep -q "@$"; then
    btrfs subvolume delete /mnt/@
fi
btrfs su cr /mnt/@
umount -R /mnt
mount -o subvol=/@ "$seed_device" /mnt

# ask user to input packages to install
read -r -p "Enter packages to install (space-separated): " packages_input

# convert input string to array
packages=($packages_input)

if [[ ${#packages[@]} -eq 0 ]]; then
   packages=(base linux linux-firmware btrfs-progs nano sudo networkmanager efibootmgr grub os-prober base-devel git)
   echo "No packages specified. Defaulting to: ${packages[@]}"
else
   echo "The following packages will be installed: ${packages[@]}"
fi

read -r -p "Continue with installation? (Yes/no): " response
# default to yes
if [[ "$response" == "no" || "$response" == "No" ]]; then
    echo "Aborting."
    exit 1
fi

pacstrap -K /mnt ${packages[@]}
mount -m "$efi_device" /mnt/efi
genfstab -U /mnt > /mnt/etc/fstab
# set root and user passwords
echo "root:root" | arch-chroot /mnt chpasswd
arch-chroot /mnt useradd -m -G wheel -s /usr/bin/bash zeev
echo "zeev:zeev" | arch-chroot /mnt chpasswd
# arch-chroot /mnt grub-install --target=x86_64-efi --efi-directory=/efi --bootloader-id=GRUB
# arch-chroot /mnt grub-mkconfig -o /boot/grub/grub.cfg
# arch-chroot /mnt systemctl enable NetworkManager
