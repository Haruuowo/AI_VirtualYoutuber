"""
60 Hz 6-DOF Virtual VR Head & Hand Pose + Native Oculus Touch Locomotion Controller for Luna (`driver_luna.dll`).

Streams 25 little-endian floats (100 bytes) over UDP to `127.0.0.1:27015`:
  - HMD (Head): 3D position (x, y, z) + quaternion (qw, qx, qy, qz)
    -> Calibrated at `h_y = 1.68m` (matching VRChat's `PlayerHeight = 1.675m` so her
       legs stand 100% straight without knee-bending!).
    -> Allows real 3D head tilting (cutesy-smug head cock), nodding, shaking head,
       leaning forward to inspect you, and turning her neck independently of her feet!
  - Left & Right Hand Controllers: 3D position + quaternion
    -> Even on fingerless chibi avatars like 'Colibri Blind', VRChat's 3-point VR IK
       moves her entire arms, elbows, and hands in 3D space (2-stage 3D hug, hands on
       hips, waving, pointing/swinging her stick, shrugging, covering cheeks when
       blushing, clapping, and gesturing naturally with her hands while talking!).
  - Locomotion [21..24]: (left_joy_x, left_joy_y, right_joy_x, right_a_click)
    -> Drives native Oculus Touch thumbsticks + A-button jump directly in SteamVR!
"""
import math
import socket
import struct
import threading
import time


def euler_deg_to_quat(pitch_x: float, yaw_y: float, roll_z: float) -> tuple[float, float, float, float]:
    """Converts (pitch_x, yaw_y, roll_z) in degrees to (qw, qx, qy, qz)."""
    rx = math.radians(pitch_x) * 0.5
    ry = math.radians(yaw_y) * 0.5
    rz = math.radians(roll_z) * 0.5

    cx, sx = math.cos(rx), math.sin(rx)
    cy, sy = math.cos(ry), math.sin(ry)
    cz, sz = math.cos(rz), math.sin(rz)

    # Y * X * Z intrinsic order (yaw -> pitch -> roll)
    qw = cy * cx * cz + sy * sx * sz
    qx = cy * sx * cz + sy * cx * sz
    qy = sy * cx * cz - cy * sx * sz
    qz = cy * cx * sz - sy * sx * cz
    return (qw, qx, qy, qz)


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * max(0.0, min(1.0, t))


class VirtualVRPoseController:
    """Background 60 Hz procedural 6-DOF VR Head + Hands + Joystick Locomotion animator."""

    def __init__(self, host: str = "127.0.0.1", port: int = 27015):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.speaking = False
        self.current_action = "idle"
        self.action_until = 0.0
        self.action_started = 0.0
        self.gaze_yaw_deg = 0.0   # Driven by YOLOv8 person tracking (-30..+30 deg)
        self._loco_strafe = 0.0
        self._loco_forward = 0.0
        self._loco_turn = 0.0
        self._loco_jump = 0.0
        self._speech_env: list[float] = []
        self._speech_start_ts = 0.0
        self._speech_fps = 60.0
        self._running = False
        self._thread: threading.Thread | None = None

    def start(self):
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self):
        self._running = False

    def set_speaking(self, speaking: bool):
        self.speaking = bool(speaking)
        if not self.speaking:
            self._speech_env = []
        elif self._speech_env:
            self._speech_start_ts = time.monotonic()

    def set_speech_envelope(self, samples, samplerate: int):
        """
        Extracts a 60 Hz smoothed RMS energy envelope (0.0..1.0) from pre-rendered TTS audio
        so Luna's head nods, hand gestures, and torso bounce sync to her real spoken syllables.
        """
        try:
            import numpy as np
            arr = np.asarray(samples, dtype=np.float32)
            if arr.ndim == 2:
                arr = arr[:, 0]
            arr = arr.ravel()
            step = max(1, int(round(samplerate / self._speech_fps)))
            n_frames = len(arr) // step
            if n_frames <= 0:
                self._speech_env = []
                return
            frames = arr[: n_frames * step].reshape(n_frames, step)
            rms = np.sqrt(np.mean(frames * frames, axis=1))
            nonzero = rms[rms > 0.005]
            ref = float(np.percentile(nonzero, 92)) if len(nonzero) > 0 else 0.1
            ref = max(ref, 0.02)
            norm = np.clip(rms / ref, 0.0, 1.2)
            # Smooth attack/release so arm & head motion flows organically
            smoothed = []
            prev = 0.0
            for v in norm.tolist():
                alpha = 0.45 if v > prev else 0.22
                prev = prev + alpha * (min(1.0, v) - prev)
                smoothed.append(prev)
            self._speech_env = smoothed
            self._speech_start_ts = time.monotonic()
        except Exception:
            self._speech_env = []

    def _current_speech_energy(self, t: float) -> float:
        if not self._speech_env:
            return 0.5 + 0.5 * math.sin(t * 5.2)
        idx = int((t - self._speech_start_ts) * self._speech_fps)
        if 0 <= idx < len(self._speech_env):
            return self._speech_env[idx]
        return 0.0

    def set_gaze_offset(self, offset_norm: float):
        """Points her 3D VR head directly toward the detected person (-1.0 left .. +1.0 right)."""
        self.gaze_yaw_deg = float(max(-35.0, min(35.0, -offset_norm * 30.0)))

    def set_locomotion(
        self,
        strafe: float | None = None,
        forward: float | None = None,
        turn: float | None = None,
        jump: bool | None = None,
    ):
        """Sets native Oculus Touch thumbstick & jump button inputs streamed at 60 Hz."""
        if strafe is not None:
            self._loco_strafe = float(max(-1.0, min(1.0, strafe)))
        if forward is not None:
            self._loco_forward = float(max(-1.0, min(1.0, forward)))
        if turn is not None:
            self._loco_turn = float(max(-1.0, min(1.0, turn)))
        if jump is not None:
            self._loco_jump = 1.0 if jump else 0.0

    def reset_locomotion(self):
        """Zeroes all SteamVR joystick & jump inputs."""
        self._loco_strafe = 0.0
        self._loco_forward = 0.0
        self._loco_turn = 0.0
        self._loco_jump = 0.0

    def trigger_pose(self, action: str, duration: float = 2.8):
        self.current_action = action
        self.action_started = time.monotonic()
        self.action_until = self.action_started + max(0.5, duration)

    def _compute_poses(self, t: float) -> list[float]:
        # Base standing eye height = 1.68m (matches VRChat PlayerHeight=1.675m so legs stand 100% straight!)
        breath = math.sin(t * 1.9) * 0.015
        sway_x = math.sin(t * 0.85) * 0.028

        # Default cutesy-smug idle pose (lively breathing, gentle sway, & expressive head tilt):
        h_x, h_y, h_z = sway_x, 1.68 + breath, 0.0
        h_pitch = math.sin(t * 1.1) * 4.0
        h_yaw = self.gaze_yaw_deg + math.sin(t * 0.55) * 4.5
        h_roll = 9.5 * math.sin(t * 0.70)  # Cutesy-smug head tilt!

        # Sassy relaxed hands at waist/hip height with subtle organic idle shift
        l_x, l_y, l_z = -0.23 + sway_x * 0.7, 1.19 + breath * 1.1 + math.sin(t * 1.2) * 0.02, -0.16
        r_x, r_y, r_z =  0.23 + sway_x * 0.7, 1.19 + breath * 1.1 + math.cos(t * 1.2) * 0.02, -0.16
        l_pitch, l_yaw, l_roll = 18.0 + math.sin(t * 1.1) * 4.0,  12.0,  20.0
        r_pitch, r_yaw, r_roll = 18.0 + math.cos(t * 1.1) * 4.0, -12.0, -20.0

        act = self.current_action if t < self.action_until else "idle"
        dt = t - self.action_started

        # ── Active 6-DOF VR Body & Arm Gestures ────────────────────────────
        if act == "stare_smug":
            # Classic sassy hands-on-hips + pronounced 16-deg smug head tilt
            h_z = -0.05
            h_pitch = -4.0
            h_roll = 16.0
            l_x, l_y, l_z = -0.24, 1.16, -0.06
            r_x, r_y, r_z =  0.24, 1.16, -0.06
            l_roll, r_roll = 35.0, -35.0

        elif act == "sassy_react":
            # Lean head back + throw both hands up in a dramatic "excuse me?!" shrug
            h_z = 0.04
            h_pitch = 8.0
            h_roll = -14.0
            shrug = math.sin(dt * 5.0) * 0.03
            l_x, l_y, l_z = -0.34, 1.44 + shrug, -0.24
            r_x, r_y, r_z =  0.34, 1.44 + shrug, -0.24
            l_pitch, r_pitch = 35.0, 35.0
            l_roll, r_roll = 40.0, -40.0

        elif act == "wave":
            # Raise right arm high and wave hand left-right in 3D space + cute head tilt
            h_roll = 12.0
            h_pitch = 4.0
            wave_x = math.sin(dt * 9.5) * 0.13
            r_x, r_y, r_z = 0.26 + wave_x, 1.68, -0.30
            r_pitch, r_yaw, r_roll = 65.0, 0.0, -wave_x * 160.0
            l_x, l_y, l_z = -0.23, 1.16, -0.07

        elif act in ("point", "stick"):
            # Extend right arm straight forward at the user (or wave/bonk with her stick!)
            h_roll = -10.0
            h_z = -0.04
            bob = math.sin(dt * 8.0) * 0.05 if act == "stick" else math.sin(dt * 4.0) * 0.02
            r_x, r_y, r_z = 0.10, 1.54 + bob, -0.48
            r_pitch, r_yaw, r_roll = 5.0 + bob * 180.0, 8.0, 0.0
            l_x, l_y, l_z = -0.24, 1.16, -0.06

        elif act == "clap":
            # Bring both hands in front of chest and clap them together in 3D
            h_pitch = 5.0
            h_roll = math.sin(dt * 6.0) * 8.0
            clap_gap = 0.04 + abs(math.sin(dt * 11.0)) * 0.11
            l_x, l_y, l_z = -clap_gap, 1.46, -0.32
            r_x, r_y, r_z =  clap_gap, 1.46, -0.32
            l_pitch, l_yaw, l_roll = 45.0, -35.0, 0.0
            r_pitch, r_yaw, r_roll = 45.0,  35.0, 0.0

        elif act == "cheer":
            # Both arms raised high above head cheering!
            bounce = abs(math.sin(dt * 7.0)) * 0.06
            h_pitch = 10.0
            l_x, l_y, l_z = -0.24, 1.82 + bounce, -0.20
            r_x, r_y, r_z =  0.24, 1.82 + bounce, -0.20
            l_pitch, r_pitch = 80.0, 80.0

        elif act in ("blush", "headpat"):
            # Both hands up covering her blushing cheeks + bashful head tilt
            wiggle = math.sin(dt * 7.5) * 0.02
            h_pitch = -8.0
            h_roll = 14.0 + wiggle * 200.0
            l_x, l_y, l_z = -0.11, 1.58 + wiggle, -0.18
            r_x, r_y, r_z =  0.11, 1.58 - wiggle, -0.18
            l_pitch, r_pitch = 60.0, 60.0

        elif act == "hug":
            # 2-phase 3D VR Hug:
            # Phase 1 (0..0.85s): Open both arms wide inviting you in
            # Phase 2 (0.85s+): Lean forward, wrap both arms around you, and sway gently
            if dt < 0.85:
                p = dt / 0.85
                h_roll = _lerp(0.0, 10.0, p)
                h_pitch = _lerp(0.0, 4.0, p)
                l_x, l_y, l_z = _lerp(-0.22, -0.38, p), _lerp(1.18, 1.50, p), _lerp(-0.14, -0.26, p)
                r_x, r_y, r_z = _lerp( 0.22,  0.38, p), _lerp(1.18, 1.50, p), _lerp(-0.14, -0.26, p)
                l_pitch, r_pitch = 25.0, 25.0
                l_yaw, r_yaw = 20.0, -20.0
            else:
                wrap_p = min(1.0, (dt - 0.85) / 0.55)
                cuddle_sway = math.sin((dt - 0.85) * 3.2) * 0.025
                h_x = cuddle_sway
                h_z = _lerp(-0.02, -0.08, wrap_p)
                h_pitch = _lerp(4.0, -6.0, wrap_p)
                h_roll = 14.0 + cuddle_sway * 180.0
                l_x = _lerp(-0.38, -0.09, wrap_p) + cuddle_sway
                r_x = _lerp( 0.38,  0.09, wrap_p) + cuddle_sway
                l_y = _lerp(1.50, 1.48, wrap_p)
                r_y = _lerp(1.50, 1.46, wrap_p)
                l_z = _lerp(-0.26, -0.44, wrap_p)
                r_z = _lerp(-0.26, -0.44, wrap_p)
                l_pitch, r_pitch = 20.0, 20.0
                l_yaw = _lerp(20.0, -45.0, wrap_p)
                r_yaw = _lerp(-20.0, 45.0, wrap_p)
                l_roll, r_roll = -15.0, 15.0

        elif act == "boop":
            # Reach right hand out to boop your nose
            reach = math.sin(min(1.0, dt / 1.5) * math.pi) * 0.18
            h_roll = 10.0
            r_x, r_y, r_z = 0.04, 1.58, -0.34 - reach
            r_pitch = 10.0

        elif act == "dance":
            # Full 3D upper-body idol dance: head & arms swaying and pumping to the beat
            beat = dt * 6.5
            h_x = math.sin(beat * 0.5) * 0.05
            h_y = 1.68 + abs(math.sin(beat)) * 0.02
            h_roll = math.sin(beat * 0.5) * 16.0
            l_x = -0.24 + math.sin(beat * 0.5) * 0.08
            r_x =  0.24 + math.sin(beat * 0.5) * 0.08
            l_y = 1.48 + math.sin(beat) * 0.15
            r_y = 1.48 - math.sin(beat) * 0.15
            l_z, r_z = -0.28, -0.28
            l_pitch, r_pitch = 45.0, 45.0

        elif act == "look_around":
            # Turn 3D head wide left then right while holding right hand near brow
            scan = math.sin(dt * 2.4) * 32.0
            h_yaw = scan
            h_roll = scan * 0.25
            r_x, r_y, r_z = 0.14, 1.70, -0.20
            r_pitch, r_yaw = 70.0, scan * 0.5

        # ── Audio-Synced Conversational Hand & Head Talking Overlay ────────
        if self.speaking and act in ("idle", "cutesy_idle", "stare_smug"):
            # Driven by live 60 Hz RMS speech envelope so nods & hand gestures match real syllables!
            energy = self._current_speech_energy(t)
            talk_t = t * 4.2
            h_pitch += -energy * 6.5 + math.sin(talk_t) * (1.5 + energy * 3.0)
            h_roll += math.cos(talk_t * 0.7) * (2.5 + energy * 5.5)
            h_z -= energy * 0.025
            lift = 0.03 + energy * 0.14
            l_y = _lerp(l_y, 1.24 + lift * 0.7 + math.sin(talk_t) * 0.03 * energy, 0.80)
            r_y = _lerp(r_y, 1.26 + lift + math.cos(talk_t * 1.1) * 0.04 * energy, 0.88)
            l_z = _lerp(l_z, -0.22 - energy * 0.08 + math.cos(talk_t) * 0.03, 0.80)
            r_z = _lerp(r_z, -0.24 - energy * 0.10 + math.sin(talk_t) * 0.04, 0.88)
            l_x = -0.22 - energy * 0.07 * abs(math.sin(talk_t * 0.8))
            r_x =  0.22 + energy * 0.08 * abs(math.cos(talk_t * 0.9))
            l_pitch = 22.0 + energy * 18.0
            r_pitch = 24.0 + energy * 22.0

        hq = euler_deg_to_quat(h_pitch, h_yaw, h_roll)
        lq = euler_deg_to_quat(l_pitch, l_yaw, l_roll)
        rq = euler_deg_to_quat(r_pitch, r_yaw, r_roll)

        return [
            h_x, h_y, h_z, *hq,
            l_x, l_y, l_z, *lq,
            r_x, r_y, r_z, *rq,
        ]

    def _compute_fbt_poses(self, t: float) -> list[float]:
        """Computes 6-DOF Waist + Left Foot + Right Foot tracker poses (21 floats)."""
        act = self.current_action if t < self.action_until else "idle"
        dt = t - self.action_started
        breath = math.sin(t * 1.9) * 0.006
        sway = math.sin(t * 0.85) * 0.018

        # Default standing hip + feet
        w_x, w_y, w_z = sway, 0.96 + breath, 0.0
        w_pitch, w_yaw, w_roll = 0.0, self.gaze_yaw_deg * 0.25, sway * 160.0

        lf_x, lf_y, lf_z = -0.10, 0.06, 0.0
        rf_x, rf_y, rf_z =  0.10, 0.06, 0.0
        lf_pitch, lf_yaw, lf_roll = 0.0, -4.0, 0.0
        rf_pitch, rf_yaw, rf_roll = 0.0,  4.0, 0.0

        if act == "stare_smug":
            # Sassy hip cock to one side
            w_x, w_roll = 0.035, -6.0
        elif act == "dance":
            beat = dt * 6.5
            w_x = math.sin(beat * 0.5) * 0.055
            w_y = 0.96 + abs(math.sin(beat)) * 0.035
            w_roll = math.sin(beat * 0.5) * 9.0
            lf_y = 0.06 + max(0.0, math.sin(beat)) * 0.09
            rf_y = 0.06 + max(0.0, -math.sin(beat)) * 0.09
            lf_z = -max(0.0, math.sin(beat)) * 0.06
            rf_z = -max(0.0, -math.sin(beat)) * 0.06
        elif self.speaking and act in ("idle", "cutesy_idle", "stare_smug"):
            energy = self._current_speech_energy(t)
            w_x += math.sin(t * 3.1) * 0.015 * (0.4 + energy)
            w_roll += math.sin(t * 3.1) * 3.5 * (0.4 + energy)

        # Walking foot-stepping cycle when moving via joystick
        walk_mag = max(abs(self._loco_forward), abs(self._loco_strafe))
        if walk_mag > 0.05:
            stride = t * 8.5
            lf_y = 0.06 + max(0.0, math.sin(stride)) * 0.07 * walk_mag
            rf_y = 0.06 + max(0.0, -math.sin(stride)) * 0.07 * walk_mag
            lf_z = -math.cos(stride) * 0.14 * self._loco_forward
            rf_z =  math.cos(stride) * 0.14 * self._loco_forward

        if self._loco_jump > 0.5:
            w_y += 0.14
            lf_y += 0.18
            rf_y += 0.18

        wq = euler_deg_to_quat(w_pitch, w_yaw, w_roll)
        lfq = euler_deg_to_quat(lf_pitch, lf_yaw, lf_roll)
        rfq = euler_deg_to_quat(rf_pitch, rf_yaw, rf_roll)
        return [
            w_x, w_y, w_z, *wq,
            lf_x, lf_y, lf_z, *lfq,
            rf_x, rf_y, rf_z, *rfq,
        ]

    def _loop(self):
        while self._running:
            t = time.monotonic()
            floats = self._compute_poses(t)
            floats.extend([
                self._loco_strafe,
                self._loco_forward,
                self._loco_turn,
                self._loco_jump,
            ])
            floats.extend(self._compute_fbt_poses(t))
            try:
                pkt = struct.pack("<46f", *floats)
                self.sock.sendto(pkt, self.addr)
            except Exception:
                pass
            time.sleep(1.0 / 60.0)
