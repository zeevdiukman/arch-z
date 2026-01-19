#!/bin/bash

set -e

# Configuration variables (will be populated via user selection or defaults if user press enter for each input)
selected_disk="/dev/vda"
seed_device="/dev/vda1"
sprout_device="/dev/vda2"
efi_device="/dev/vda3"

default_packages=(
    base 
    linux 
    linux-firmware 
    btrfs-progs 
    nano 
    sudo 
    networkmanager 
    efibootmgr 
    grub 
    os-prober 
    base-devel 
    git
)

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

# Get Sprout PARTUUID for bootloader configuration
sprout_partuuid=$(blkid -s PARTUUID -o value "$sprout_device")
echo "Sprout PARTUUID: $sprout_partuuid"

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
    mkfs.btrfs -f -L SPROUT "$sprout_device"
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
   packages=(${default_packages[@]})
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

# User Configuration
echo "--- System Configuration ---"
read -r -p "Enter hostname (default: arch-z): " target_hostname
target_hostname=${target_hostname:-arch-z}

read -r -p "Enter username (default: zeev): " target_user
target_user=${target_user:-zeev}

read -r -p "Enter timezone (default: Europe/Helsinki): " target_timezone
target_timezone=${target_timezone:-Europe/Helsinki}

echo "Set root password:"
while true; do
    read -r -s -p "Password: " root_pass
    echo
    read -r -s -p "Confirm Password: " root_pass_confirm
    echo
    [ "$root_pass" = "$root_pass_confirm" ] && break
    echo "Passwords do not match. Try again."
done

echo "Set password for user $target_user:"
while true; do
    read -r -s -p "Password: " user_pass
    echo
    read -r -s -p "Confirm Password: " user_pass_confirm
    echo
    [ "$user_pass" = "$user_pass_confirm" ] && break
    echo "Passwords do not match. Try again."
done



chroot_function() {
    mkinitcpio_hooks=(
        base
        udev
        autodetect
        microcode
        modconf
        kms
        keyboard
        block
        btrfs
        filesystems
    )
    grub_install_options=(
        --target=x86_64-efi
        --efi-directory=/efi
        --boot-directory=/boot
        --bootloader-id=GRUB
    )
    installation_lines_array=(
    "hwclock --systohc"
    "echo '$target_hostname' > /etc/hostname"
    "echo 'KEYMAP=us' > /etc/vconsole.conf"
    "sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen"
    "locale-gen"
    "echo 'LANG=en_US.UTF-8' > /etc/locale.conf"
    "ln -sf /usr/share/zoneinfo/$target_timezone /etc/localtime"
    "sed -i \"s/^HOOKS=.*/HOOKS=(${mkinitcpio_hooks[*]})/\" /etc/mkinitcpio.conf"
    "echo 'root:$root_pass' | chpasswd"
    "useradd -m -G wheel -s /usr/bin/bash $target_user"
    "echo '$target_user:$user_pass' | chpasswd"
    "echo '$target_user ALL=(ALL:ALL) ALL' > /etc/sudoers.d/$target_user"
    "systemctl enable systemd-timesyncd"
    "grub-install ${grub_install_options[*]}"
    "echo 'GRUB_DISABLE_OS_PROBER=false' >> /etc/default/grub"
    "grub-mkconfig -o /boot/grub/grub.cfg"
    "sed -i 's/root=UUID=[A-Fa-f0-9-]*/root=PARTUUID=$sprout_partuuid/g' /boot/grub/grub.cfg"
    "passwd -l root"
    "mkinitcpio -P"
    )
    
    local script
    script=$(printf "%s\n" "${installation_lines_array[@]}")
    chrt -- "$script"
}

chroot_function

#################################################3
echo "--- Finalizing Seed/Sprout setup ---"
echo "Unmounting /mnt..."
umount -R /mnt
echo "Converting $seed_device to a seed device..."
btrfstune -S 1 "$seed_device"
echo "Mounting seed device to add sprout..."
mount -o subvol=/@ "$seed_device" /mnt
echo "Adding $sprout_device as sprout device..."
btrfs device add -f "$sprout_device" /mnt
echo "Unmounting and remounting sprout device..."
umount -R /mnt
mount -o subvol=/@ "$sprout_device" /mnt
echo "Mounting EFI partition..."
mount -m "$efi_device" /mnt/efi
echo "Generating final fstab with PARTUUIDs..."
genfstab -t PARTUUID /mnt > /mnt/etc/fstab

echo ""
echo "################################################################"
echo "#                   INSTALLATION COMPLETE                      #"
echo "################################################################"
echo ""
read -r -p "Do you want to reboot now? (y/N): " reboot_ans
if [[ "$reboot_ans" =~ ^[Yy]$ ]]; then
    reboot
else
    echo "You can reboot manually by typing 'reboot'."
fi