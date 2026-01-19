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

# ... (imports continue)

# ... (existing code)

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

# ... (perform_installation continues)

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
    
    # ... (skipping to end of chroot)
    
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
