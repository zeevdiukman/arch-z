#!/bin/bash

# test_z.sh - Mock testing environment for z.sh

MOCK_DIR=$(mktemp -d)
MOCK_BIN="$MOCK_DIR/bin"
mkdir -p "$MOCK_BIN"
export PATH="$MOCK_BIN:$PATH"

LOG_FILE="$MOCK_DIR/test_log.txt"
touch "$LOG_FILE"

echo "Using mock directory: $MOCK_DIR"

# Mock lsblk
cat > "$MOCK_BIN/lsblk" <<'EOF'
#!/bin/bash
if [[ "$*" == *"-p -dno NAME,SIZE,MODEL"* ]]; then
    echo "/dev/vda 100G Mock_Disk"
    echo "/dev/vdb 50G Mock_Secondary"
elif [[ "$*" == *"-p -nlo NAME,SIZE,TYPE /dev/vda"* ]]; then
    echo "/dev/vda1 1G part"
    echo "/dev/vda2 20G part"
    echo "/dev/vda3 512M part"
elif [[ "$*" == *"-p -nlo NAME,SIZE,TYPE /dev/vdb"* ]]; then
    echo "/dev/vdb1 50G part"
fi
EOF

# Mock functions to log calls
mock_cmd() {
    local cmd="$1"
    cat > "$MOCK_BIN/$cmd" <<-EOF
#!/bin/bash
echo "[$cmd] \$*" >> "$LOG_FILE"
EOF
    chmod +x "$MOCK_BIN/$cmd"
}

mock_cmd "mkfs.btrfs"
mock_cmd "mkfs.fat"
mock_cmd "mount"
mock_cmd "umount"
mock_cmd "pacstrap"
mock_cmd "genfstab"
mock_cmd "useradd"
mock_cmd "chpasswd"

# Mock btrfs with logic
cat > "$MOCK_BIN/btrfs" <<EOF
#!/bin/bash
echo "[btrfs] \$*" >> "$LOG_FILE"
if [[ "\$*" == "subvolume list /mnt" ]]; then
    # Simulate @ existing
    echo "ID 256 gen 10 top level 5 path @"
fi
EOF

# Mock mountpoint
cat > "$MOCK_BIN/mountpoint" <<EOF
#!/bin/bash
echo "[mountpoint] \$*" >> "$LOG_FILE"
return 1 # Simulate not mounted
EOF

# Mock arch-chroot
cat > "$MOCK_BIN/arch-chroot" <<EOF
#!/bin/bash
# Capture stdin if any
if [[ -p /dev/stdin ]]; then
    stdin_content=\$(cat)
    echo "[arch-chroot] stdin: \$stdin_content | cmd: \$*" >> "$LOG_FILE"
else
    echo "[arch-chroot] cmd: \$*" >> "$LOG_FILE"
fi
EOF

chmod +x "$MOCK_BIN/"*

echo "--- Starting z.sh in Mock Environment ---"
# Run z.sh and provide sequence of inputs for test scenario 1 (Default settings)
# Input sequence:
# 1 (Enter) - Select Disk 1
# 1 (Enter) - Select Seed
# 1 (Enter) - Select Sprout
# 1 (Enter) - Select EFI
# yes - Confirm formatting
# (Enter) - Default packages
# Yes - Confirm installation
printf "\n\n\n\nyes\n\nYes\n" | bash ./z.sh

echo ""
echo "--- Test Execution Log ---"
cat "$LOG_FILE"

# Clean up
# rm -rf "$MOCK_DIR"
echo ""
echo "Logs saved to $LOG_FILE"
