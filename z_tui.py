
import sys
import threading
from textual.app import App, ComposeResult
from textual.containers import Container, Vertical, Horizontal
from textual.widgets import Header, Footer, Button, Static, Label, Input, Select, DataTable, Log, ListItem, ListView
from textual.screen import Screen, ModalScreen
from textual.message import Message
from textual import on, work

# Import logic from z.py
# We need to add the current directory to path if not already there
import os
sys.path.append(os.getcwd())
import z

class InstallWork(threading.Thread):
    def __init__(self, config, log_callback):
        super().__init__()
        self.config = config
        self.log_callback = log_callback

    def run(self):
        z.perform_installation(self.config, log_func=self.log_callback)

class DiskSelectScreen(Screen):
    """Screen to select the target disk."""
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Label("Select Storage Disk", classes="title"),
            DataTable(id="disk_table"),
            Button("Next", variant="primary", id="btn_next", disabled=True),
        )
        yield Footer()

    def on_mount(self) -> None:
        table = self.query_one(DataTable)
        table.cursor_type = "row"
        table.add_columns("Name", "Size", "Model")
        
        disks = z.get_disks()
        self.disks_data = disks # keep reference
        
        rows = []
        for d in disks:
            rows.append((d['name'], d['size'], d['model']))
        
        table.add_rows(rows)

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        table = self.query_one(DataTable)
        self.app.selected_disk = self.disks_data[event.cursor_row]['name']
        self.query_one("#btn_next").disabled = False
        self.app.push_screen("partition_select")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            self.app.push_screen("partition_select")

class PartitionSelectScreen(Screen):
    """Screen to select partitions for Seed, Sprout, and EFI."""
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Label(f"Select Partitions on {self.app.selected_disk}", classes="title"),
            Vertical(
                Label("Seed Partition (Read-Only Base):"),
                Select([], id="sel_seed"),
                Label("Sprout Partition (Writable Layer):"),
                Select([], id="sel_sprout"),
                Label("EFI Partition (Boot):"),
                Select([], id="sel_efi"),
                classes="form-group"
            ),
            Button("Next", variant="primary", id="btn_next"),
        )
        yield Footer()

    def on_mount(self) -> None:
        parts = z.get_partitions(self.app.selected_disk)
        # Format for Select: (label, value)
        options = [(p['display'], p['path']) for p in parts]
        
        self.query_one("#sel_seed").set_options(options)
        self.query_one("#sel_sprout").set_options(options)
        self.query_one("#sel_efi").set_options(options)
        
        # Try to set sensible defaults if standard layout
        # (This is a simplified attempt matching z.py defaults logic)
        for _, val in options:
            if val == f"{self.app.selected_disk}1":
                self.query_one("#sel_seed").value = val
            elif val == f"{self.app.selected_disk}2":
                 self.query_one("#sel_sprout").value = val
            elif val == f"{self.app.selected_disk}3":
                 self.query_one("#sel_efi").value = val
    
    def on_select_changed(self, event: Select.Changed) -> None:
        if event.control.id == "sel_seed":
             self.query_one("#sel_sprout").focus()
        elif event.control.id == "sel_sprout":
             self.query_one("#sel_efi").focus()
        elif event.control.id == "sel_efi":
             self.query_one("#btn_next").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            self.app.seed_device = self.query_one("#sel_seed").value
            self.app.sprout_device = self.query_one("#sel_sprout").value
            self.app.efi_device = self.query_one("#sel_efi").value
            
            if not all([self.app.seed_device, self.app.sprout_device, self.app.efi_device]):
                self.notify("Please select all partitions", severity="error")
                return
                
            self.app.push_screen("config")

class ConfigScreen(Screen):
    """Screen for collecting user configuration."""
    
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Label("System Configuration", classes="title"),
            Vertical(
                Label("Hostname:"),
                Input(value="arch-z", id="inp_hostname"),
                Label("Username:"),
                Input(value="zeev", id="inp_user"),
                Label("Timezone:"),
                Input(value="Europe/Helsinki", id="inp_timezone"),
                
                Label("Root Password:"),
                Input(password=True, id="inp_root_pass"),
                Label("Root Password (Confirm):"),
                Input(password=True, id="inp_root_pass_confirm"),
                
                Label("User Password:"),
                Input(password=True, id="inp_user_pass"),
                Label("User Password (Confirm):"),
                Input(password=True, id="inp_user_pass_confirm"),
                
                classes="form-group"
            ),
            Button("Next", variant="primary", id="btn_next"),
        )
        yield Footer()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        ids = ["inp_hostname", "inp_user", "inp_timezone", "inp_root_pass", "inp_root_pass_confirm", "inp_user_pass", "inp_user_pass_confirm"]
        current_id = event.control.id
        if current_id in ids:
            idx = ids.index(current_id)
            if idx < len(ids) - 1:
                self.query_one(f"#{ids[idx+1]}").focus()
            else:
                self.query_one("#btn_next").press()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_next":
            # Validation
            r1 = self.query_one("#inp_root_pass").value
            r2 = self.query_one("#inp_root_pass_confirm").value
            u1 = self.query_one("#inp_user_pass").value
            u2 = self.query_one("#inp_user_pass_confirm").value
            
            if r1 != r2:
                self.notify("Root passwords do not match", severity="error")
                return
            if u1 != u2:
                self.notify("User passwords do not match", severity="error")
                return
            if not r1 or not u1:
                self.notify("Passwords cannot be empty", severity="error")
                return
                
            self.app.conf_hostname = self.query_one("#inp_hostname").value
            self.app.conf_username = self.query_one("#inp_user").value
            self.app.conf_timezone = self.query_one("#inp_timezone").value
            self.app.conf_root_pass = r1
            self.app.conf_user_pass = u1
            
            self.app.push_screen("packages")

class PackageScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Label("Select Packages", classes="title"),
            Label("Edit the list of packages to install (space separated):"),
            Input(value=" ".join(z.default_packages), id="inp_packages"),
            Button("Review Summary", variant="primary", id="btn_next"),
        )
        yield Footer()
    
    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.query_one("#btn_next").press()
        
    def on_button_pressed(self, event: Button.Pressed) -> None:
         if event.button.id == "btn_next":
             pkg_str = self.query_one("#inp_packages").value
             self.app.packages = pkg_str.split() if pkg_str else z.default_packages
             self.app.push_screen("summary")

class SummaryScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Label("Configuration Summary", classes="title"),
            Static(id="summary_text"),
            Horizontal(
                Button("Install", variant="error", id="btn_install"),
                Button("Quit", variant="default", id="btn_quit"),
                classes="buttons"
            )
        )
        yield Footer()
        
    def on_mount(self):
        text = f"""
        Disk: {self.app.selected_disk}
        Seed: {self.app.seed_device}
        Sprout: {self.app.sprout_device}
        EFI: {self.app.efi_device}
        
        Hostname: {self.app.conf_hostname}
        User: {self.app.conf_username}
        Timezone: {self.app.conf_timezone}
        
        Packages: {len(self.app.packages)} selected
        """
        self.query_one("#summary_text").update(text)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_quit":
            self.app.exit()
        elif event.button.id == "btn_install":
            self.app.push_screen("install")

class InstallScreen(Screen):
    def compose(self) -> ComposeResult:
        yield Header()
        yield Container(
            Label("Installing...", classes="title"),
            Log(id="install_log"),
            Button("Done", id="btn_done", disabled=True)
        )
        
    def on_mount(self):
        log = self.query_one(Log)
        
        config = z.InstallConfig(
            seed_device=self.app.seed_device,
            sprout_device=self.app.sprout_device,
            efi_device=self.app.efi_device,
            hostname=self.app.conf_hostname,
            username=self.app.conf_username,
            timezone=self.app.conf_timezone,
            root_password=self.app.conf_root_pass,
            user_password=self.app.conf_user_pass,
            packages=self.app.packages,
            # For testing safety, you might want to default to dry_run logic or prompt.
            # But the user asked for the real deal. I'll add a safety switch.
            dry_run=False 
        )
        
        self.worker = InstallWork(config, self.write_log)
        self.worker.start()
        self.timer = self.set_interval(0.5, self.check_done)

    def write_log(self, message):
        self.query_one(Log).write_line(message)
        
    def check_done(self):
        if not self.worker.is_alive():
            self.timer.stop()
            self.query_one("#btn_done").disabled = False
            self.query_one(Log).write_line("--- Process Finished ---")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "btn_done":
            self.app.exit()

class ZInstallerApp(App):
    CSS = """
    Screen {
        align: center middle;
    }
    .title {
        text-align: center;
        text-style: bold;
        margin: 1;
    }
    .form-group {
        margin: 1 2;
        height: auto;
        border: round $primary;
        padding: 1;
    }
    DataTable {
        height: 1fr;
        border: solid $secondary;
    }
    .install-container {
        height: 100%;
        width: 100%;
        align: center middle; 
    }
    #install_log {
        height: 1fr;
        border: solid $secondary;
        margin: 1;
        background: $surface;
    }
    .buttons {
        align: center middle;
        height: auto;
        margin-top: 1;
    }
    Button {
        margin: 1;
    }
    """
    
    selected_disk = None
    seed_device = None
    sprout_device = None
    efi_device = None
    
    conf_hostname = None
    conf_username = None
    conf_timezone = None
    conf_root_pass = None
    conf_user_pass = None
    packages = []

    SCREENS = {
        "disk_select": DiskSelectScreen,
        "partition_select": PartitionSelectScreen,
        "config": ConfigScreen,
        "packages": PackageScreen,
        "summary": SummaryScreen,
        "install": InstallScreen
    }

    def on_mount(self) -> None:
        self.push_screen("disk_select")

if __name__ == "__main__":
    app = ZInstallerApp()
    app.run()
