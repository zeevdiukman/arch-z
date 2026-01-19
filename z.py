#!/usr/bin/env python3

import subprocess
import sys
import os
import shlex
import getpass
import shutil
import time
from dataclasses import dataclass, field
from typing import List, Optional

# Configuration variables
default_packages = [
    "base", "linux", "linux-firmware", "btrfs-progs", "nano", "sudo",
    "networkmanager", "efibootmgr", "grub", "os-prober", "base-devel", "git"
]

@dataclass
class InstallConfig:
    seed_device: str
    sprout_device: str
    efi_device: str
    hostname: str = "arch-z"
    username: str = "zeev"
    timezone: str = "Europe/Helsinki"
    root_password: str = ""
    user_password: str = ""
    packages: List[str] = field(default_factory=lambda: list(default_packages))
    dry_run: bool = False

def run_command(command, check=True, shell=False, capture_output=False, dry_run=False):
    """Result wrapper for subprocess.run"""
    if dry_run:
        print(f"[DRY RUN] Would execute: {command}")
        # Return a dummy completed process for dry runs so logic doesn't crash on attribute access
        return subprocess.CompletedProcess(args=command, returncode=0, stdout="", stderr="")

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

def run_live_command(command, log_func=None, check=True, shell=False, dry_run=False):
    """Executes a command and streams output to log_func."""
    if dry_run:
        if log_func:
            log_func(f"[DRY RUN] Would execute: {command}")
        return

    # Use shell=True if command is a string, consistent with run_command logic preference
    # though run_command defaults shell=False. We follow the caller's instructions.
    
    # Needs to handle list vs string same as run_command
    if isinstance(command, str) and not shell:
         cmd_list = shlex.split(command)
    else:
         cmd_list = command

    process = subprocess.Popen(
        cmd_list,
        shell=shell,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1 # Line buffered
    )
    
    if log_func:
        for line in process.stdout:
            log_func(line.rstrip())
            
    return_code = process.wait()
    if check and return_code != 0:
        if log_func:
             log_func(f"Command failed with return code {return_code}")
        # Mimic subprocess.CalledProcessError
        raise subprocess.CalledProcessError(return_code, command)

def check_dependencies(log_func=print):
    """Checks if required system tools are available."""
    required_tools = [
        "lsblk", "btrfs", "mkfs.btrfs", "mkfs.fat", 
        "pacstrap", "genfstab", "arch-chroot"
    ]
    missing = []
    for tool in required_tools:
        if not shutil.which(tool):
            missing.append(tool)
    
    if missing:
        msg = f"Error: Missing required tools: {', '.join(missing)}\nPlease install: btrfs-progs, dosfstools, arch-install-scripts"
        log_func(msg)
        raise RuntimeError(msg)

def cleanup_mount(mount_point, log_func=print):
    """
    Robustly unmounts a path, killing processes if necessary.
    """
    log_func(f"Unmounting {mount_point}...")
    
    # First try normal unmount
    ret = subprocess.run(f"umount -R {mount_point}", shell=True, stderr=subprocess.DEVNULL)
    if ret.returncode == 0:
        return True

    log_func(f"Unmount failed. Checking for busy processes on {mount_point}...")
    
    # Check for fuser
    if shutil.which("fuser"):
        log_func("Killing processes accessing the mount point...")
        subprocess.run(f"fuser -k -m {mount_point}", shell=True)
        time.sleep(1) # Give them a second to die
    else:
         log_func("Warning: 'fuser' not found. Cannot automatically kill busy processes.")

    # Retry unmount
    ret = subprocess.run(f"umount -R {mount_point}", shell=True)
    if ret.returncode == 0:
        return True
        
    # Last resort: Lazy unmount
    log_func("Force/Lazy unmounting...")
    ret = subprocess.run(f"umount -R -l {mount_point}", shell=True)
    if ret.returncode != 0:
        log_func(f"Critical: Failed to unmount {mount_point} even with lazy unmount.")
        return False
    return True

def perform_installation(config: InstallConfig, log_func=print):
    """
    Executes the installation process based on the provided configuration.
    log_func is a function to display messages (defaults to print).
    """
    
    try:
        check_dependencies(log_func)
    except RuntimeError:
        # If check fails, we stop. In TUI this will define the error.
        return

    # helper for dry_run propagation
    def run(cmd, **kwargs):
        # Merge dry_run into kwargs if not explicitly set
        if 'dry_run' not in kwargs:
            kwargs['dry_run'] = config.dry_run
            
        # If capturing output, we use the standard run_command (buffered)
        if kwargs.get('capture_output'):
            return run_command(cmd, **kwargs)
        
        # Otherwise, use live command for streaming logs
        return run_live_command(cmd, log_func=log_func, **kwargs)

    log_func("--- Starting Installation ---")
    
    # Get Sprout PARTUUID
    if config.dry_run:
        sprout_partuuid = "DRY-RUN-UUID-1234"
    else:
        cmd_uuid = f"blkid -s PARTUUID -o value {config.sprout_device}"
        sprout_partuuid = run(cmd_uuid, shell=True, capture_output=True).stdout.strip()
    
    log_func(f"Sprout PARTUUID: {sprout_partuuid}")
    
    # Check mountpoint
    # For dry_run we might want to skip real checks or mock them
    if not config.dry_run:
        mount_check = subprocess.run(["mountpoint", "-q", "/mnt"], check=False)
        if mount_check.returncode == 0:
            log_func("/mnt is already mounted. Unmounting...")
            cleanup_mount("/mnt", log_func)

    log_func("Creating filesystems...")
    run(f"mkfs.btrfs -f -L SEED {config.seed_device}", shell=True)
    run(f"mkfs.btrfs -f -L SPROUT {config.sprout_device}", shell=True)
    run(f"mkfs.fat -F 32 -n EFI {config.efi_device}", shell=True)
    log_func("Filesystems created successfully.")
        
    # Initial Mount
    run(f"mount -o subvol=/ {config.seed_device} /mnt", shell=True)
    
    # Check for @ subvolume
    if not config.dry_run:
        subvol_list = run("btrfs subvolume list /mnt", shell=True, capture_output=True).stdout
        if any(line.endswith(" @") or line.endswith("path @") for line in subvol_list.splitlines()):
            run("btrfs subvolume delete /mnt/@", shell=True)
        
    run("btrfs su cr /mnt/@", shell=True)
    cleanup_mount("/mnt", log_func)
    run(f"mount -o subvol=/@ {config.seed_device} /mnt", shell=True)
    
    # Pacstrap
    log_func(f"Installing packages: {' '.join(config.packages)}")
    run(["pacstrap", "-K", "/mnt"] + config.packages)
    run(f"mount -m {config.efi_device} /mnt/efi", shell=True)
    
    # fstab
    if not config.dry_run:
        with open("/mnt/etc/fstab", "w") as f:
            subprocess.run(["genfstab", "-U", "/mnt"], stdout=f, check=True)
    else:
        log_func("[DRY RUN] Would generate fstab")
        
    # Chroot function
    mkinitcpio_hooks = "base udev autodetect microcode modconf kms keyboard block btrfs filesystems"
    grub_options = "--target=x86_64-efi --efi-directory=/efi --boot-directory=/boot --bootloader-id=GRUB"
    
    install_script = [
        "hwclock --systohc",
        f"echo '{config.hostname}' > /etc/hostname",
        "echo 'KEYMAP=us' > /etc/vconsole.conf",
        "sed -i 's/^#en_US.UTF-8 UTF-8/en_US.UTF-8 UTF-8/' /etc/locale.gen",
        "locale-gen",
        "echo 'LANG=en_US.UTF-8' > /etc/locale.conf",
        f"ln -sf /usr/share/zoneinfo/{config.timezone} /etc/localtime",
        f'sed -i "s/^HOOKS=.*/HOOKS=({mkinitcpio_hooks})/" /etc/mkinitcpio.conf',
        f"echo 'root:{config.root_password}' | chpasswd",
        f"useradd -m -G wheel -s /usr/bin/bash {config.username}",
        f"echo '{config.username}:{config.user_password}' | chpasswd",
        f"echo '{config.username} ALL=(ALL:ALL) ALL' > /etc/sudoers.d/{config.username}",
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
    run(["arch-chroot", "/mnt", "/usr/bin/bash", "-c", full_script])
    
    # Give processes a moment to release handles (e.g. gpg-agent)
    time.sleep(2)
    
    # Final cleanup
    log_func("--- Finalizing Seed/Sprout setup ---")
    cleanup_mount("/mnt", log_func)
    
    log_func(f"Converting {config.seed_device} to a seed device...")
    run(f"btrfstune -S 1 {config.seed_device}", shell=True)
    
    log_func("Mounting seed device to add sprout...")
    run(f"mount -o subvol=/@ {config.seed_device} /mnt", shell=True)
    
    log_func(f"Adding {config.sprout_device} as sprout device...")
    run(f"btrfs device add -f {config.sprout_device} /mnt", shell=True)
    
    log_func("Unmounting and remounting sprout device...")
    cleanup_mount("/mnt", log_func)
    run(f"mount -o subvol=/@ {config.sprout_device} /mnt", shell=True)
    
    log_func("Mounting EFI partition...")
    run(f"mount -m {config.efi_device} /mnt/efi", shell=True)
    
    log_func("Generating final fstab with PARTUUIDs...")
    if not config.dry_run:
        with open("/mnt/etc/fstab", "w") as f:
             subprocess.run(["genfstab", "-t", "PARTUUID", "/mnt"], stdout=f, check=True)
    else:
        log_func("[DRY RUN] Would generate final fstab")
         
    log_func("\n################################################################")
    log_func("#                   INSTALLATION COMPLETE                      #")
    log_func("################################################################\n")

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
    # Helper defaults
    current_disk_default = "/dev/vda"
    seed_default = "/dev/vda1"
    sprout_default = "/dev/vda2"
    efi_default = "/dev/vda3"
    
    # 1. Select Disk
    print("Available storage disks:")
    disks = get_disks()
    if not disks:
        print("No disks found!")
        sys.exit(1)
        
    choice = select_option(disks, "Select a disk to choose partitions from", current_disk_default)
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

    seed_device = select_part("--- Select Seed Partition ---", "Seed device: ", seed_default)
    sprout_device = select_part("--- Select Sprout Partition ---", "Sprout device: ", sprout_default)
    efi_device = select_part("--- Select EFI Partition ---", "EFI device: ", efi_default)
    
    print("\nConfiguration Summary:")
    print(f"Seed device:   {seed_device}")
    print(f"Sprout device: {sprout_device}")
    print(f"EFI device:    {efi_device}\n")
    
    # Packages
    pkg_input = input("Enter packages to install (space-separated): ").strip()
    packages = pkg_input.split() if pkg_input else list(default_packages)
    
    if packages == default_packages:
        print(f"No packages specified. Defaulting to: {' '.join(packages)}")
    else:
        print(f"The following packages will be installed: {' '.join(packages)}")
        
    # User Configuration
    print("--- System Configuration ---")
    hostname = input("Enter hostname (default: arch-z): ").strip() or "arch-z"
    user = input("Enter username (default: zeev): ").strip() or "zeev"
    timezone = input("Enter timezone (default: Europe/Helsinki): ").strip() or "Europe/Helsinki"
    
    print("Set root password:")
    root_pass = ""
    while True:
        p1 = getpass.getpass("Password: ")
        p2 = getpass.getpass("Confirm Password: ")
        if p1 == p2:
            root_pass = p1
            break
        print("Passwords do not match. Try again.")
        
    print(f"Set password for user {user}:")
    user_pass = ""
    while True:
        p1 = getpass.getpass("Password: ")
        p2 = getpass.getpass("Confirm Password: ")
        if p1 == p2:
            user_pass = p1
            break
        print("Passwords do not match. Try again.")

    # Confirm
    resp = input("Confirm formatting and installation? (yes/no): ").lower()
    if resp not in ["yes", "y"]:
        print("Aborting.")
        sys.exit(1)

    # Build Config
    config = InstallConfig(
        seed_device=seed_device,
        sprout_device=sprout_device,
        efi_device=efi_device,
        hostname=hostname,
        username=user,
        timezone=timezone,
        root_password=root_pass,
        user_password=user_pass,
        packages=packages,
        dry_run=False # CLI default is live
    )

    # Execute
    perform_installation(config)
    
    reboot_ans = input("Do you want to reboot now? (y/N): ").lower()
    if reboot_ans in ["y", "yes"]:
        run_command("reboot", shell=True)
    else:
        print("You can reboot manually by typing 'reboot'.")

if __name__ == "__main__":
    if os.geteuid() != 0:
        print("Warning: Not running as root. Operations may fail.", file=sys.stderr)
        
    try:
        main()
    except KeyboardInterrupt:
        print("\nInterrupted.")
        sys.exit(1)
