#!/usr/bin/env python3

import subprocess
import sys
import os
import shlex
import getpass

# Configuration variables
selected_disk = "/dev/vda"
seed_device = "/dev/vda1"
sprout_device = "/dev/vda2"
efi_device = "/dev/vda3"

default_packages = [
    "base", "linux", "linux-firmware", "btrfs-progs", "nano", "sudo",
    "networkmanager", "efibootmgr", "grub", "os-prober", "base-devel", "git"
]

def run_command(command, check=True, shell=False, capture_output=False):
    """Result wrapper for subprocess.run"""
    try:
        # If command is a string and shell is False, split it (naive splitting)
        # But better to rely on caller passing list if shell=False
        if isinstance(command, str) and not shell:
            cmd_list = shlex.split(command)
        else:
            cmd_list = command
            
        result = subprocess.run(
            cmd_list,
            check=check,
            shell=shell,
            text=True,
            capture_output=capture_output
        )
        return result
    except subprocess.CalledProcessError as e:
        print(f"Error executing command: {command}")
        print(f"Error output: {e.stderr}")
        if check:
            sys.exit(1)
        return e

def get_disks():
    """Returns a list of dictionaries with disk info."""
    cmd = ["lsblk", "-p", "-dno", "NAME,SIZE,MODEL"]
    result = run_command(cmd, capture_output=True)
    disks = []
    if result.stdout:
        lines = result.stdout.strip().split('\n')
        for line in lines:
            parts = line.split(maxsplit=2)
            if len(parts) >= 2:
                name = parts[0]
                size = parts[1]
                model = parts[2] if len(parts) > 2 else ""
                disks.append({"name": name, "size": size, "model": model, "raw": line})
    return disks

def get_partitions(disk):
    """Returns a list of partitions for the given disk."""
    # lsblk -p -nlo NAME,SIZE,TYPE "$selected_disk" | awk '$3=="part" {printf "%s (%s)\n", $1, $2}'
    cmd = f"lsblk -p -nlo NAME,SIZE,TYPE {disk}"
    result = run_command(cmd, shell=True, capture_output=True)
    parts = []
    if result.stdout:
        lines = result.stdout.strip().split('\n')
        for line in lines:
            # We want to match awk '$3=="part"' logic
            columns = line.split()
            if len(columns) >= 3 and columns[2] == "part":
                # Create display string "NAME (SIZE)"
                display = f"{columns[0]} ({columns[1]})"
                parts.append({"path": columns[0], "display": display})
    return parts

def select_option(options, prompt_text, default_val=None):
    """Generic selection loop"""
    if not options:
        print("No options available!")
        sys.exit(1)
        
    for i, opt in enumerate(options):
        # opt can be a dict or string, we display it accordingly
        display = opt['display'] if isinstance(opt, dict) and 'display' in opt else str(opt)
        # If it's the raw disk line from earlier, use that
        if isinstance(opt, dict) and 'raw' in opt:
            display = opt['raw']
            
        print(f"{i + 1}) {display}")

    default_idx = 1
    if default_val:
        for i, opt in enumerate(options):
            val_to_check = opt['name'] if isinstance(opt, dict) and 'name' in opt else \
                           (opt['path'] if isinstance(opt, dict) and 'path' in opt else str(opt))
            # Check prefix match like input script
            if str(val_to_check).startswith(default_val):
                default_idx = i + 1
                break
    
    while True:
        try:
            choice = input(f"{prompt_text} (default {default_idx}): ").strip()
        except KeyboardInterrupt:
            sys.exit(1)
            
        if not choice:
            choice = str(default_idx)
        
        if choice.isdigit():
            idx = int(choice)
            if 1 <= idx <= len(options):
                return options[idx - 1]
        
        print("Invalid selection.")

def main():
    global selected_disk, seed_device, sprout_device, efi_device
    
    # 1. Select Disk
    print("Available storage disks:")
    disks = get_disks()
    if not disks:
        print("No disks found!")
        sys.exit(1)
        
    choice = select_option(disks, "Select a disk to choose partitions from", selected_disk)
    selected_disk = choice['name']
    
    # 2. Select Partitions
    def select_part(header, prompt, current_val):
        print(f"\n{header}")
        parts = get_partitions(selected_disk)
        if not parts:
            print(f"No partitions found on {selected_disk}!")
            sys.exit(1)
        
        choice = select_option(parts, prompt, current_val)
        return choice['path']

    seed_device = select_part("--- Select Seed Partition ---", "Seed device: ", seed_device)
    sprout_device = select_part("--- Select Sprout Partition ---", "Sprout device: ", sprout_device)
    efi_device = select_part("--- Select EFI Partition ---", "EFI device: ", efi_device)
    
    # Get Sprout PARTUUID
    cmd_uuid = f"blkid -s PARTUUID -o value {sprout_device}"
    sprout_partuuid = run_command(cmd_uuid, shell=True, capture_output=True).stdout.strip()
    print(f"Sprout PARTUUID: {sprout_partuuid}")
    
    print("\nConfiguration Summary:")
    print(f"Seed device:   {seed_device}")
    print(f"Sprout device: {sprout_device}")
    print(f"EFI device:    {efi_device}\n")
    
    resp = input("Confirm formatting and installation? (yes/no/skip): ").lower()
    if resp not in ["yes", "y", "skip"]:
        print("Aborting.")
        sys.exit(1)
        
    # Check mountpoint
    mount_check = subprocess.run(["mountpoint", "-q", "/mnt"], check=False)
    if mount_check.returncode == 0:
        print("/mnt is already mounted. Unmounting...")
        run_command("umount -R /mnt", check=False)
        
    if resp == "skip":
        print("Skipping formatting")
    else:
        run_command(f"mkfs.btrfs -f -L SEED {seed_device}", shell=True)
        run_command(f"mkfs.btrfs -f -L SPROUT {sprout_device}", shell=True)
        run_command(f"mkfs.fat -F 32 -n EFI {efi_device}", shell=True)
        print("Filesystems created successfully.")
        
    # Initial Mount
    run_command(f"mount -o subvol=/ {seed_device} /mnt", shell=True)
    
    # Check for @ subvolume
    subvol_list = run_command("btrfs subvolume list /mnt", shell=True, capture_output=True).stdout
    if any(line.endswith(" @") or line.endswith("path @") for line in subvol_list.splitlines()):
        run_command("btrfs subvolume delete /mnt/@", shell=True)
        
    run_command("btrfs su cr /mnt/@", shell=True)
    run_command("umount -R /mnt", shell=True)
    run_command(f"mount -o subvol=/@ {seed_device} /mnt", shell=True)
    
    # Packages
    pkg_input = input("Enter packages to install (space-separated): ").strip()
    packages = pkg_input.split() if pkg_input else default_packages
    
    if packages == default_packages:
        print(f"No packages specified. Defaulting to: {' '.join(packages)}")
    else:
        print(f"The following packages will be installed: {' '.join(packages)}")
        
    cont = input("Continue with installation? (Yes/no): ").lower()
    if cont == "no":
        print("Aborting.")
        sys.exit(1)
        
    # Pacstrap
    run_command(["pacstrap", "-K", "/mnt"] + packages)
    run_command(f"mount -m {efi_device} /mnt/efi", shell=True)
    
    # fstab
    with open("/mnt/etc/fstab", "w") as f:
        subprocess.run(["genfstab", "-U", "/mnt"], stdout=f, check=True)
        
    # User Configuration
    print("--- System Configuration ---")
    hostname = input("Enter hostname (default: arch-z): ").strip() or "arch-z"
    user = input("Enter username (default: zeev): ").strip() or "zeev"
    timezone = input("Enter timezone (default: Europe/Helsinki): ").strip() or "Europe/Helsinki"
    
    print("Set root password:")
    while True:
        p1 = getpass.getpass("Password: ")
        p2 = getpass.getpass("Confirm Password: ")
        if p1 == p2:
            root_pass = p1
            break
        print("Passwords do not match. Try again.")
        
    print(f"Set password for user {user}:")
    while True:
        p1 = getpass.getpass("Password: ")
        p2 = getpass.getpass("Confirm Password: ")
        if p1 == p2:
            user_pass = p1
            break
        print("Passwords do not match. Try again.")
        
    # Chroot function
    mkinitcpio_hooks = "base udev autodetect microcode modconf kms keyboard block btrfs filesystems"
    grub_options = "--target=x86_64-efi --efi-directory=/efi --boot-directory=/boot --bootloader-id=GRUB"
    
    install_script = [
        "hwclock --systohc",
        f"echo '{hostname}' > /etc/hostname",
        "echo 'KEYMAP=us' > /etc/vconsole.conf",
        "sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen",
        "locale-gen",
        "echo 'LANG=en_US.UTF-8' > /etc/locale.conf",
        f"ln -sf /usr/share/zoneinfo/{timezone} /etc/localtime",
        f'sed -i "s/^HOOKS=.*/HOOKS=({mkinitcpio_hooks})/" /etc/mkinitcpio.conf',
        f"echo 'root:{root_pass}' | chpasswd",
        f"useradd -m -G wheel -s /usr/bin/bash {user}",
        f"echo '{user}:{user_pass}' | chpasswd",
        f"echo '{user} ALL=(ALL:ALL) ALL' > /etc/sudoers.d/{user}",
        "systemctl enable systemd-timesyncd",
        f"grub-install {grub_options}",
        "echo 'GRUB_DISABLE_OS_PROBER=false' >> /etc/default/grub",
        "grub-mkconfig -o /boot/grub/grub.cfg",
        f"sed -i 's/root=UUID=[A-Fa-f0-9-]*/root=PARTUUID={sprout_partuuid}/g' /boot/grub/grub.cfg",
        "passwd -l root",
        "mkinitcpio -P"
    ]
    
    full_script = "\n".join(install_script)
    # arch-chroot /mnt /usr/bin/bash -c "$cmd"
    # We pass the full script as one argument to bash -c
    run_command(["arch-chroot", "/mnt", "/usr/bin/bash", "-c", full_script])
    
    # Final cleanup
    print("--- Finalizing Seed/Sprout setup ---")
    print("Unmounting /mnt...")
    run_command("umount -R /mnt", shell=True)
    
    print(f"Converting {seed_device} to a seed device...")
    run_command(f"btrfstune -S 1 {seed_device}", shell=True)
    
    print("Mounting seed device to add sprout...")
    run_command(f"mount -o subvol=/@ {seed_device} /mnt", shell=True)
    
    print(f"Adding {sprout_device} as sprout device...")
    run_command(f"btrfs device add -f {sprout_device} /mnt", shell=True)
    
    print("Unmounting and remounting sprout device...")
    run_command("umount -R /mnt", shell=True)
    run_command(f"mount -o subvol=/@ {sprout_device} /mnt", shell=True)
    
    print("Mounting EFI partition...")
    run_command(f"mount -m {efi_device} /mnt/efi", shell=True)
    
    print("Generating final fstab with PARTUUIDs...")
    with open("/mnt/etc/fstab", "w") as f:
         subprocess.run(["genfstab", "-t", "PARTUUID", "/mnt"], stdout=f, check=True)
         
    print("\n################################################################")
    print("#                   INSTALLATION COMPLETE                      #")
    print("################################################################\n")
    
    reboot_ans = input("Do you want to reboot now? (y/N): ").lower()
    if reboot_ans in ["y", "yes"]:
        run_command("reboot", shell=True)
    else:
        print("You can reboot manually by typing 'reboot'.")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
