#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Project: Controller Merger
Module: controller_merger.py
Author: Martinius Ulberg
Contact: MarUlberg@gmail.com
License: MIT License
Description:
    Combines inputs from up to four physical Xbox controllers
    into one virtual Xbox 360 controller.

    The virtual controller acts as Player 1, allowing all
    connected players to control The Quarry simultaneously.
"""

import ctypes
import math
import time
import atexit
import subprocess
import tkinter as tk
from pathlib import Path

import pygame
import vgamepad as vg
from PIL import Image, ImageOps, ImageTk
from pygame._sdl2 import controller as sdl_controller


# ============================================================
# Configuration
# ============================================================

MAX_CONTROLLERS = 4
POLL_RATE = 120
DEADZONE = 0.12
RESCAN_INTERVAL = 1.0
VIRTUAL_DEVICE_TIMEOUT = 10.0
DISCONNECT_SETTLE_TIME = 2.0
POWERSHELL_TIMEOUT = 30


# ============================================================
# Controller Button Mapping
# ============================================================

BUTTON_MAP = (
    (pygame.CONTROLLER_BUTTON_A,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_A),

    (pygame.CONTROLLER_BUTTON_B,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_B),

    (pygame.CONTROLLER_BUTTON_X,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_X),

    (pygame.CONTROLLER_BUTTON_Y,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_Y),

    (pygame.CONTROLLER_BUTTON_BACK,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_BACK),

    (pygame.CONTROLLER_BUTTON_START,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_START),

    (pygame.CONTROLLER_BUTTON_LEFTSHOULDER,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_SHOULDER),

    (pygame.CONTROLLER_BUTTON_RIGHTSHOULDER,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_SHOULDER),

    (pygame.CONTROLLER_BUTTON_LEFTSTICK,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_LEFT_THUMB),

    (pygame.CONTROLLER_BUTTON_RIGHTSTICK,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_RIGHT_THUMB),

    (pygame.CONTROLLER_BUTTON_DPAD_UP,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_UP),

    (pygame.CONTROLLER_BUTTON_DPAD_DOWN,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_DOWN),

    (pygame.CONTROLLER_BUTTON_DPAD_LEFT,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_LEFT),

    (pygame.CONTROLLER_BUTTON_DPAD_RIGHT,
     vg.XUSB_BUTTON.XUSB_GAMEPAD_DPAD_RIGHT),
)


# ============================================================
# Controller Discovery
# ============================================================

def get_device_instances():
    """Return SDL instance IDs and GUIDs for currently visible joysticks."""

    pygame.event.pump()
    devices = {}

    for index in range(sdl_controller.get_count()):
        joystick = pygame.joystick.Joystick(index)
        devices[joystick.get_instance_id()] = joystick.get_guid()

    return devices


def identify_virtual_controller(existing_devices, virtual_gamepad):
    """Identify the newly created virtual controller by its SDL instance ID."""

    deadline = time.monotonic() + VIRTUAL_DEVICE_TIMEOUT
    expected_ids = (virtual_gamepad.get_vid(), virtual_gamepad.get_pid())

    while time.monotonic() < deadline:
        devices = get_device_instances()
        added = devices.keys() - existing_devices.keys()
        removed = existing_devices.keys() - devices.keys()

        if removed or len(added) > 1:
            raise RuntimeError(
                "Controller devices changed while identifying the virtual "
                "controller. Restart without connecting or disconnecting devices."
            )

        if added:
            instance_id = added.pop()
            guid = bytes.fromhex(devices[instance_id])
            actual_ids = (
                int.from_bytes(guid[4:6], "little"),
                int.from_bytes(guid[8:10], "little"),
            )
            if actual_ids != expected_ids:
                raise RuntimeError(
                    "A different controller appeared while identifying the "
                    "virtual controller. Restart without connecting devices."
                )
            return instance_id

        time.sleep(0.05)

    raise RuntimeError(
        "The virtual controller did not appear in pygame; cannot safely "
        "identify it. Restart the application."
    )


def discover_controllers(virtual_instance_id, existing_controllers=None,
                         virtual_guid=None):
    """Find physical controllers while preserving existing open handles."""

    controllers = {}
    existing = dict(existing_controllers or {})

    for index in range(sdl_controller.get_count()):

        if not sdl_controller.is_controller(index):
            continue

        try:
            joystick = pygame.joystick.Joystick(index)
            instance_id = joystick.get_instance_id()

            # Skip our virtual pad, including copies that re-enumerate with
            # a new instance ID or come from another running instance.
            if instance_id == virtual_instance_id:
                continue
            if virtual_guid and joystick.get_guid() == virtual_guid:
                continue

            controller = existing.pop(instance_id, None)
            if controller is None:
                controller = sdl_controller.Controller(index)
                print(
                    f"[CONNECTED] Controller {len(controllers) + 1}: "
                    f"{joystick.get_name()}"
                )

            controllers[instance_id] = controller

            if len(controllers) == MAX_CONTROLLERS:
                break

        except pygame.error as error:
            print(f"[WARNING] Controller {index}: {error}")

    close_controllers(existing.values())
    return controllers


def close_controllers(controllers):
    """Safely close all initialized physical controllers."""

    for controller in controllers:
        try:
            controller.quit()
        except pygame.error:
            pass


# ============================================================
# Temporary Controller Disconnect
# ============================================================

_disabled_device_ids = []


def run_powershell(script):
    """Run a PowerShell script and return its stdout, or None on failure."""

    try:
        result = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True,
            text=True,
            timeout=POWERSHELL_TIMEOUT,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.SubprocessError) as error:
        print(f"[WARNING] PowerShell failed: {error}")
        return None

    if result.returncode != 0:
        print(f"[WARNING] PowerShell error: {result.stderr.strip()}")
        return None

    return result.stdout


def is_admin():
    """Return True when the script is running with administrator rights."""

    try:
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except (AttributeError, OSError):
        return False


def disable_physical_controllers():
    """Disable present XInput controllers so the virtual one gets Player 1."""

    if not is_admin():
        print(
            "[WARNING] Not running as administrator; cannot temporarily "
            "disconnect controllers. Connect them after starting the script."
        )
        return

    listing = run_powershell(
        "Get-PnpDevice -PresentOnly -Status OK | Where-Object { "
        "$_.Class -in 'HIDClass','XnaComposite','XboxComposite' -and "
        "$_.FriendlyName -match 'XINPUT|Xbox' -and "
        "$_.InstanceId -notlike 'ROOT\\*' } | "
        "ForEach-Object { $_.InstanceId }"
    )
    device_ids = [line.strip() for line in (listing or "").splitlines()
                  if line.strip()]

    if not device_ids:
        return

    print(f"[INFO] Temporarily disconnecting {len(device_ids)} controller "
          "device(s)...")

    # Register first so devices are restored even if the script crashes.
    atexit.register(restore_physical_controllers)

    for device_id in device_ids:
        if run_powershell(
            f"Disable-PnpDevice -InstanceId '{device_id}' -Confirm:$false"
        ) is not None:
            _disabled_device_ids.append(device_id)

    time.sleep(DISCONNECT_SETTLE_TIME)


def restore_physical_controllers():
    """Re-enable every controller device disabled by this script."""

    if not _disabled_device_ids:
        return

    print("[INFO] Reconnecting physical controllers...")

    while _disabled_device_ids:
        device_id = _disabled_device_ids[0]
        if run_powershell(
            f"Enable-PnpDevice -InstanceId '{device_id}' -Confirm:$false"
        ) is None:
            print(f"[WARNING] Could not re-enable {device_id}; enable it "
                  "manually in Device Manager.")
        _disabled_device_ids.pop(0)


# ============================================================
# Analog Stick Merging
# ============================================================

def get_strongest_stick(controllers, axis_x, axis_y):
    """Return the strongest complete analog stick vector."""

    strongest_x = 0.0
    strongest_y = 0.0
    strongest_magnitude = 0.0

    for controller in controllers:

        try:
            if not controller.attached():
                continue

            x = controller.get_axis(axis_x) / 32767.0
            y = -controller.get_axis(axis_y) / 32767.0

            x = max(-1.0, min(1.0, x))
            y = max(-1.0, min(1.0, y))

            magnitude = math.sqrt(x * x + y * y)

            if magnitude <= DEADZONE:
                continue

            if magnitude > strongest_magnitude:

                scaled = min(
                    1.0,
                    (magnitude - DEADZONE) / (1.0 - DEADZONE)
                )

                strongest_x = (x / magnitude) * scaled
                strongest_y = (y / magnitude) * scaled

                strongest_magnitude = magnitude

        except pygame.error:
            continue

    return strongest_x, strongest_y


# ============================================================
# Trigger Merging
# ============================================================

def get_max_trigger(controllers, axis):
    """Return the highest trigger pressure from all controllers."""

    highest = 0.0

    for controller in controllers:

        try:
            if not controller.attached():
                continue

            value = controller.get_axis(axis) / 32767.0

            highest = max(highest, value)

        except pygame.error:
            continue

    return max(0.0, min(1.0, highest))


# ============================================================
# Button Merging
# ============================================================

def button_pressed(controllers, button):
    """Check whether any connected controller presses a button."""

    for controller in controllers:

        try:
            if controller.attached() and controller.get_button(button):
                return True

        except pygame.error:
            continue

    return False


# ============================================================
# Input Processing
# ============================================================

def merge_inputs(controllers, virtual_gamepad):
    """Merge all physical controller inputs into virtual Player 1."""

    # Start with a completely neutral controller state.
    virtual_gamepad.reset()

    # --------------------------------------------------------
    # Analog sticks
    # --------------------------------------------------------

    left_x, left_y = get_strongest_stick(
        controllers,
        pygame.CONTROLLER_AXIS_LEFTX,
        pygame.CONTROLLER_AXIS_LEFTY
    )

    right_x, right_y = get_strongest_stick(
        controllers,
        pygame.CONTROLLER_AXIS_RIGHTX,
        pygame.CONTROLLER_AXIS_RIGHTY
    )

    virtual_gamepad.left_joystick_float(
        x_value_float=left_x,
        y_value_float=left_y
    )

    virtual_gamepad.right_joystick_float(
        x_value_float=right_x,
        y_value_float=right_y
    )

    # --------------------------------------------------------
    # Triggers
    # --------------------------------------------------------

    left_trigger = get_max_trigger(
        controllers,
        pygame.CONTROLLER_AXIS_TRIGGERLEFT
    )

    right_trigger = get_max_trigger(
        controllers,
        pygame.CONTROLLER_AXIS_TRIGGERRIGHT
    )

    virtual_gamepad.left_trigger_float(
        value_float=left_trigger
    )

    virtual_gamepad.right_trigger_float(
        value_float=right_trigger
    )

    # --------------------------------------------------------
    # Buttons
    # --------------------------------------------------------

    for physical_button, virtual_button in BUTTON_MAP:

        if button_pressed(controllers, physical_button):

            virtual_gamepad.press_button(
                button=virtual_button
            )

    # Send the combined state to Windows.
    virtual_gamepad.update()


# ============================================================
# Main Application
# ============================================================

class StatusWindow:
    """Window showing four controllers with connection status squares."""

    IMAGE_PATH = Path(__file__).with_name("controller.png")
    ICON_PATH = Path(__file__).with_name("icon.ico")
    IMAGE_WIDTH = 100
    PADDING = 10
    STATUS_SIZE = 5
    STATUS_GAP = 5
    COLOR_CONNECTED = "#00b050"
    COLOR_DISCONNECTED = "#e00000"

    def __init__(self):
        """Build the white window, controller images and status squares."""
        self.closed = False
        self.root = tk.Tk()
        self.root.title("Controller Status")
        self.root.iconbitmap(default=str(self.ICON_PATH))
        self.root.configure(bg="white")
        self.root.resizable(False, False)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        image = Image.open(self.IMAGE_PATH).convert("RGBA")
        height = round(image.height * self.IMAGE_WIDTH / image.width)
        # Resize premultiplied so transparent black pixels don't bleed into edges.
        image = (
            image.convert("RGBa")
            .resize((self.IMAGE_WIDTH, height), Image.LANCZOS)
            .convert("RGBA")
        )
        self.photo_connected = ImageTk.PhotoImage(image)
        self.photo_disconnected = ImageTk.PhotoImage(
            self.make_disconnected_image(image)
        )

        pad = self.PADDING
        width = pad + MAX_CONTROLLERS * (self.IMAGE_WIDTH + pad)
        total_height = pad * 2 + height + self.STATUS_GAP + self.STATUS_SIZE
        self.canvas = tk.Canvas(
            self.root, width=width, height=total_height,
            bg="white", highlightthickness=0
        )
        self.canvas.pack()
        self.canvas.bind("<Button-1>", self.open_game_controller_panel)
        self.canvas.configure(cursor="hand2")
        self.images = []
        self.indicators = []
        for index in range(MAX_CONTROLLERS):
            left = pad + index * (self.IMAGE_WIDTH + pad)
            self.images.append(self.canvas.create_image(
                left, pad, image=self.photo_disconnected, anchor="nw"
            ))
            square_left = left + (self.IMAGE_WIDTH - self.STATUS_SIZE) // 2
            square_top = pad + height + self.STATUS_GAP
            self.indicators.append(self.canvas.create_rectangle(
                square_left, square_top,
                square_left + self.STATUS_SIZE, square_top + self.STATUS_SIZE,
                fill=self.COLOR_DISCONNECTED, outline=""
            ))

    @staticmethod
    def open_game_controller_panel(event=None):
        """Open the Windows Game Controllers panel (joy.cpl) for button testing."""
        try:
            subprocess.Popen(["control.exe", "joy.cpl"])
        except OSError as error:
            print(f"[WARNING] Could not open game controller panel: {error}")

    @staticmethod
    def make_disconnected_image(image):
        """Return a grayscale, color-inverted copy of the image, keeping alpha."""
        alpha = image.getchannel("A")
        negative = ImageOps.invert(image.convert("L"))
        negative.putalpha(alpha)
        return negative.convert("RGBA")

    def set_connected_count(self, count):
        """Color the first `count` squares green and the rest red."""
        for index, (item, picture) in enumerate(
            zip(self.indicators, self.images)
        ):
            connected = index < count
            self.canvas.itemconfigure(
                item,
                fill=self.COLOR_CONNECTED if connected
                else self.COLOR_DISCONNECTED
            )
            self.canvas.itemconfigure(
                picture,
                image=self.photo_connected if connected
                else self.photo_disconnected
            )

    def update(self):
        """Process pending GUI events without blocking."""
        if not self.closed:
            try:
                self.root.update()
            except tk.TclError:
                self.closed = True

    def close(self):
        """Mark the window closed and destroy it."""
        if not self.closed:
            self.closed = True
            try:
                self.root.destroy()
            except tk.TclError:
                pass


def main():
    """Initialize the virtual controller and run the input merger."""

    print("=" * 55)
    print("       QUARRY CONTROLLER MERGER")
    print("=" * 55)

    disable_physical_controllers()

    print("[INFO] Initializing pygame...")

    pygame.init()
    sdl_controller.init()

    existing_devices = get_device_instances()

    print("[INFO] Creating virtual Xbox 360 controller...")

    virtual_gamepad = vg.VX360Gamepad()

    print("[READY] Virtual Xbox 360 controller created.")

    controllers = {}
    last_scan = 0.0
    last_count = -1
    last_controller_ids = None
    window = StatusWindow()

    try:
        virtual_instance_id = identify_virtual_controller(
            existing_devices, virtual_gamepad
        )
        virtual_guid = get_device_instances().get(virtual_instance_id)
        # The virtual controller now holds Player 1; bring the others back.
        restore_physical_controllers()

        print("[INFO] Waiting for physical controllers...")
        print("[INFO] Press CTRL+C to stop.")
        print("-" * 55)

        while not window.closed:

            pygame.event.pump()

            current_time = time.monotonic()
            device_count = sdl_controller.get_count()

            # Rescan when devices change or a controller disconnects.
            needs_scan = (
                current_time - last_scan >= RESCAN_INTERVAL
                or device_count != last_count
                or any(not c.attached() for c in controllers.values())
            )

            if needs_scan:

                controllers = discover_controllers(
                    virtual_instance_id, controllers, virtual_guid
                )

                last_count = device_count
                last_scan = current_time
                controller_ids = set(controllers)

                if controller_ids != last_controller_ids:
                    print(
                        f"[INFO] Active controllers: "
                        f"{len(controllers)}/{MAX_CONTROLLERS}"
                    )
                last_controller_ids = controller_ids
                window.set_connected_count(len(controllers))

            window.update()

            # Combine all controller inputs.
            merge_inputs(
                controllers.values(),
                virtual_gamepad
            )

            time.sleep(1.0 / POLL_RATE)

    except KeyboardInterrupt:

        print("\n[INFO] Stopping controller merger...")

    finally:

        # Release all virtual buttons and axes.
        virtual_gamepad.reset()
        virtual_gamepad.update()

        close_controllers(controllers.values())

        window.close()
        pygame.quit()

        print("[STOPPED] Controller merger stopped.")


# ============================================================
# Application Entry Point
# ============================================================

if __name__ == "__main__":
    main()