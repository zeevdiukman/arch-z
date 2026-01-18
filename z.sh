#!/bin/bash

set -e

# Configuration variables (will be populated via user selection or defaults if user press enter for each input)
selected_disk="/dev/vda"
seed_device="/dev/vda1"
sprout_device="/dev/vda2"
efi_device="/dev/vda3"
chrt() {
    [[ "$1" == "--" ]] && shift
    local cmd="$*"
    arch-chroot /mnt /usr/bin/bash -c "$cmd"
}

# 1. Select Disk
echo "Available storage disks:"
mapfile -t disks < <(lsblk -p -dno NAME,SIZE,MODEL)
if [ ${#disks[@]} -eq 0 ]; then
    echo "No disks found!"
    exit 1
fi

for i in "${!disks[@]}"; do
    printf "%d) %s\n" "$((i+1))" "${disks[i]}"
done

while true; do
    def_idx=1
    if [[ -n "$selected_disk" ]]; then
        for i in "${!disks[@]}"; do
            if [[ "${disks[i]}" == "$selected_disk"* ]]; then
                def_idx=$((i+1))
                break
            fi
        done
    fi

    read -r -p "Select a disk to choose partitions from (default $def_idx): " REPLY
    REPLY=${REPLY:-$def_idx}
    if [[ "$REPLY" =~ ^[0-9]+$ ]] && [[ "$REPLY" -gt 0 && "$REPLY" -le "${#disks[@]}" ]]; then
        disk_info="${disks[$((REPLY-1))]}"
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
    for i in "${!parts[@]}"; do
        printf "%d) %s\n" "$((i+1))" "${parts[i]}"
    done

    while true; do
        local def_idx=1
        if [[ -n "$default_val" ]]; then
            for i in "${!parts[@]}"; do
                if [[ "${parts[i]}" == "$default_val"* ]]; then
                    def_idx=$((i+1))
                    break
                fi
            done
        fi

        read -r -p "$ps3_val (default $def_idx): " REPLY
        REPLY=${REPLY:-$def_idx}
        if [[ "$REPLY" =~ ^[0-9]+$ ]] && [[ "$REPLY" -gt 0 && "$REPLY" -le "${#parts[@]}" ]]; then
            selection="${parts[$((REPLY-1))]}"
            eval "$var_name=$(echo "$selection" | cut -d' ' -f1)"
            break
        else
            echo "Invalid selection."
        fi
    done
}

get_partition "--- Select Seed Partition ---" "Seed device: " seed_device "$seed_device"
get_partition "--- Select Sprout Partition ---" "Sprout device: " sprout_device "$sprout_device"
get_partition "--- Select EFI Partition ---" "EFI device: " efi_device "$efi_device"

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
if mountpoint -q /mnt; then
    echo "/mnt is already mounted. Unmounting..."
    umount -R /mnt
fi

if [[ "$response" == "skip" ]]; then
    echo "Skipping formatting"
else
    mkfs.btrfs -f -L SEED "$seed_device"
    mkfs.btrfs -f -L SPRUT "$sprout_device"
    mkfs.fat -F 32 -n EFI "$efi_device"
    echo "Filesystems created successfully."
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

chrt -- echo 'root:root' | chpasswd 
chrt -- useradd -m -G wheel -s /usr/bin/bash zeev
chrt -- echo 'zeev:zeev' | chpasswd 
chrt -- echo 'zeev ALL=(ALL:ALL) ALL' > /etc/sudoers.d/zeev
