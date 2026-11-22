"""
60 Hz 6-DOF Virtual VR Head & Hand Pose Controller for Luna (`driver_luna.dll`).

Streams 21 little-endian floats (84 bytes) over UDP to `127.0.0.1:27015`:
  - HMD (Head): 3D position (x, y, z) + quaternion (qw, qx, qy, qz)
    -> Allows real 3D head tilting (cutesy-smug head cock), nodding, shaking head,
       leaning forward to inspect you, and turning her neck independently of her feet!
  - Left & Right Hand Controllers: 3D position + quaternion
    -> Even on fingerless chibi avatars like 'Colibri Blind', VRChat's 3-point VR IK
       moves her entire arms, elbows, and hands in 3D space (hands on hips, waving,
       pointing/swinging her stick, shrugging, covering cheeks when blushing, clapping,
       and gesturing naturally with her hands while talking!).
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
    """Background 60 Hz procedural 6-DOF VR Head + Hands animator."""

    def __init__(self, host: str = "127.0.0.1", port: int = 27015):
        self.addr = (host, port)
        self.sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self.speaking = False
        self.current_action = "idle"
        self.action_until = 0.0
        self.action_started = 0.0
        self.gaze_yaw_deg = 0.0   # Driven by YOLOv8 person tracking (-30..+30 deg)
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

    def set_gaze_offset(self, offset_norm: float):
        """Points her 3D VR head directly toward the detected person (-1.0 left .. +1.0 right)."""
        # In OpenVR right-handed coordinates (+Y up, -Z forward, +X right), negative yaw turns right
        self.gaze_yaw_deg = float(max(-28.0, min(28.0, -offset_norm * 24.0)))

    def trigger_pose(self, action: str, duration: float = 2.8):
        self.current_action = action
        self.action_started = time.monotonic()
        self.action_until = self.action_started + max(0.5, duration)

    def _compute_poses(self, t: float) -> list[float]:
        # Base standing head height = 1.35m
        breath = math.sin(t * 1.9) * 0.012
        sway_x = math.sin(t * 0.85) * 0.015

        # Default cutesy-smug idle pose:
        # Head slightly tilted sideways (~7 deg), hands resting near hips/waist in a sassy posture
        h_x, h_y, h_z = sway_x, 1.35 + breath, 0.0
        h_pitch = math.sin(t * 1.1) * 2.5
        h_yaw = self.gaze_yaw_deg + math.sin(t * 0.5) * 3.0
        h_roll = 7.5 * math.sin(t * 0.65)  # Cutesy-smug head tilt!

        # Sassy relaxed hands (slightly flared out near waist/hips)
        l_x, l_y, l_z = -0.22 + sway_x * 0.5, 0.88 + breath * 0.7, -0.14
        r_x, r_y, r_z =  0.22 + sway_x * 0.5, 0.88 + breath * 0.7, -0.14
        l_pitch, l_yaw, l_roll = 15.0,  12.0,  18.0
        r_pitch, r_yaw, r_roll = 15.0, -12.0, -18.0

        act = self.current_action if t < self.action_until else "idle"
        dt = t - self.action_started

        # ── Active 6-DOF VR Body & Arm Gestures ────────────────────────────
        if act == "stare_smug":
            # Classic sassy hands-on-hips + pronounced 16-deg smug head tilt + lean forward
            h_z = -0.06
            h_pitch = -4.0
            h_roll = 16.0
            l_x, l_y, l_z = -0.24, 0.86, -0.06
            r_x, r_y, r_z =  0.24, 0.86, -0.06
            l_roll, r_roll = 35.0, -35.0

        elif act == "sassy_react":
            # Lean head back + throw both hands up in a dramatic "excuse me?!" shrug
            h_z = 0.05
            h_pitch = 8.0
            h_roll = -14.0
            shrug = math.sin(dt * 5.0) * 0.03
            l_x, l_y, l_z = -0.34, 1.12 + shrug, -0.24
            r_x, r_y, r_z =  0.34, 1.12 + shrug, -0.24
            l_pitch, r_pitch = 35.0, 35.0
            l_roll, r_roll = 40.0, -40.0

        elif act == "wave":
            # Raise right arm high and wave hand left-right in 3D space + cute head tilt
            h_roll = 12.0
            h_pitch = 4.0
            wave_x = math.sin(dt * 9.5) * 0.13
            r_x, r_y, r_z = 0.26 + wave_x, 1.36, -0.30
            r_pitch, r_yaw, r_roll = 65.0, 0.0, -wave_x * 160.0
            # Left hand on hip
            l_x, l_y, l_z = -0.23, 0.86, -0.07

        elif act in ("point", "stick"):
            # Extend right arm straight forward at the user (or wave/bonk with her stick!)
            h_roll = -10.0
            h_z = -0.05
            bob = math.sin(dt * 8.0) * 0.05 if act == "stick" else math.sin(dt * 4.0) * 0.02
            r_x, r_y, r_z = 0.10, 1.22 + bob, -0.48
            r_pitch, r_yaw, r_roll = 5.0 + bob * 180.0, 8.0, 0.0
            # Left hand on hip
            l_x, l_y, l_z = -0.24, 0.86, -0.06

        elif act == "clap":
            # Bring both hands in front of chest and clap them together in 3D
            h_pitch = 5.0
            h_roll = math.sin(dt * 6.0) * 8.0
            clap_gap = 0.04 + abs(math.sin(dt * 11.0)) * 0.11
            l_x, l_y, l_z = -clap_gap, 1.14, -0.32
            r_x, r_y, r_z =  clap_gap, 1.14, -0.32
            l_pitch, l_yaw, l_roll = 45.0, -35.0, 0.0
            r_pitch, r_yaw, r_roll = 45.0,  35.0, 0.0

        elif act == "cheer":
            # Both arms raised high above head cheering!
            bounce = abs(math.sin(dt * 7.0)) * 0.07
            h_y += bounce * 0.5
            h_pitch = 10.0
            l_x, l_y, l_z = -0.24, 1.50 + bounce, -0.20
            r_x, r_y, r_z =  0.24, 1.50 + bounce, -0.20
            l_pitch, r_pitch = 80.0, 80.0

        elif act in ("blush", "headpat"):
            # Both hands up covering her blushing cheeks + bashful head tilt
            wiggle = math.sin(dt * 7.5) * 0.02
            h_pitch = -8.0
            h_roll = 14.0 + wiggle * 200.0
            l_x, l_y, l_z = -0.11, 1.26 + wiggle, -0.18
            r_x, r_y, r_z =  0.11, 1.26 - wiggle, -0.18
            l_pitch, r_pitch = 60.0, 60.0

        elif act == "hug":
            # 2-phase 3D VR Hug:
            # Phase 1 (0..0.85s): Open both arms wide inviting you in
            # Phase 2 (0.85s+): Lean forward, wrap both arms around you, and sway gently
            if dt < 0.85:
                p = dt / 0.85
                h_roll = _lerp(0.0, 10.0, p)
                h_pitch = _lerp(0.0, 4.0, p)
                l_x, l_y, l_z = _lerp(-0.22, -0.38, p), _lerp(0.88, 1.18, p), _lerp(-0.14, -0.26, p)
                r_x, r_y, r_z = _lerp( 0.22,  0.38, p), _lerp(0.88, 1.18, p), _lerp(-0.14, -0.26, p)
                l_pitch, r_pitch = 25.0, 25.0
                l_yaw, r_yaw = 20.0, -20.0
            else:
                wrap_p = min(1.0, (dt - 0.85) / 0.55)
                cuddle_sway = math.sin((dt - 0.85) * 3.2) * 0.025
                h_x = cuddle_sway
                h_z = _lerp(-0.02, -0.10, wrap_p)
                h_pitch = _lerp(4.0, -6.0, wrap_p)
                h_roll = 14.0 + cuddle_sway * 180.0
                l_x = _lerp(-0.38, -0.09, wrap_p) + cuddle_sway
                r_x = _lerp( 0.38,  0.09, wrap_p) + cuddle_sway
                l_y = _lerp(1.18, 1.16, wrap_p)
                r_y = _lerp(1.18, 1.14, wrap_p)
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
            r_x, r_y, r_z = 0.04, 1.26, -0.34 - reach
            r_pitch = 10.0

        elif act == "dance":
            # Full 3D upper-body idol dance: head & arms swaying and pumping to the beat
            beat = dt * 6.5
            h_x = math.sin(beat * 0.5) * 0.06
            h_y = 1.34 + abs(math.sin(beat)) * 0.04
            h_roll = math.sin(beat * 0.5) * 16.0
            l_x = -0.24 + math.sin(beat * 0.5) * 0.08
            r_x =  0.24 + math.sin(beat * 0.5) * 0.08
            l_y = 1.15 + math.sin(beat) * 0.16
            r_y = 1.15 - math.sin(beat) * 0.16
            l_z, r_z = -0.28, -0.28
            l_pitch, r_pitch = 45.0, 45.0

        elif act == "look_around":
            # Turn 3D head wide left then right while holding right hand near brow
            scan = math.sin(dt * 2.4) * 32.0
            h_yaw = scan
            h_roll = scan * 0.25
            r_x, r_y, r_z = 0.14, 1.38, -0.20
            r_pitch, r_yaw = 70.0, scan * 0.5

        # ── Conversational Hand-Talking Overlay When Speaking ──────────────
        if self.speaking and act in ("idle", "cutesy_idle", "stare_smug"):
            # Natural VR conversational hand & head movement while she talks!
            talk_t = t * 5.2
            h_pitch += math.sin(talk_t) * 4.5
            h_roll += math.cos(talk_t * 0.7) * 5.0
            l_y = _lerp(l_y, 1.04 + math.sin(talk_t) * 0.06, 0.75)
            r_y = _lerp(r_y, 1.06 + math.cos(talk_t * 1.1) * 0.07, 0.85)
            l_z = _lerp(l_z, -0.26 + math.cos(talk_t) * 0.04, 0.75)
            r_z = _lerp(r_z, -0.28 + math.sin(talk_t) * 0.05, 0.85)
            l_x = -0.22 - abs(math.sin(talk_t * 0.8)) * 0.06
            r_x =  0.22 + abs(math.cos(talk_t * 0.9)) * 0.07
            l_pitch, r_pitch = 30.0, 32.0

        hq = euler_deg_to_quat(h_pitch, h_yaw, h_roll)
        lq = euler_deg_to_quat(l_pitch, l_yaw, l_roll)
        rq = euler_deg_to_quat(r_pitch, r_yaw, r_roll)

        return [
            h_x, h_y, h_z, *hq,
            l_x, l_y, l_z, *lq,
            r_x, r_y, r_z, *rq,
        ]

    def _loop(self):
        while self._running:
            t = time.monotonic()
            floats = self._compute_poses(t)
            try:
                pkt = struct.pack("<21f", *floats)
                self.sock.sendto(pkt, self.addr)
            except Exception:
                pass
            time.sleep(1.0 / 60.0)
