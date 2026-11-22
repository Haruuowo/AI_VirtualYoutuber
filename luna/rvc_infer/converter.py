"""
Self-Contained RVC v2 Voice Converter for Luna (Gawr Gura `GuraTalkV2.pth`).

Runs 100% locally on Python 3.11 Windows without `fairseq`:
  1. ContentVec 768-D Feature Extraction via `vec-768-layer-12.onnx` (ONNXRuntime)
  2. Praat Parselmouth (`pm`) F0 Pitch Extraction
  3. PyTorch RVC v2 NSF-HiFiGAN Generator (`SynthesizerTrnMs768NSFsid` with `GuraTalkV2.pth`)
"""
import os
import numpy as np
import onnxruntime as ort
import parselmouth
import scipy.signal
import torch
import torch.nn.functional as F

from luna.rvc_infer.models import SynthesizerTrnMs768NSFsid

BASE_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
RVC_DIR = os.path.join(BASE_DIR, "models", "rvc")
CONTENTVEC_ONNX = os.path.join(RVC_DIR, "vec-768-layer-12.onnx")
GURA_PTH = os.path.join(RVC_DIR, "gura", "GuraTalkV2.pth")
BAELZ_PTH = os.path.join(RVC_DIR, "baelz", "baetalk_e215_s6235.pth")
HENYA_PTH = os.path.join(RVC_DIR, "henya", "henya.pth")

RVC_VOICE_MODELS = {
    "henya": (
        HENYA_PTH,
        "https://huggingface.co/dacoolkid44/VTuber-RVC/resolve/main/Henya%20The%20Genius/henya.pth",
        "Henya the Genius RVC v2 (henya.pth)",
    ),
    "baelz": (
        BAELZ_PTH,
        "https://huggingface.co/dacoolkid44/VTuber-RVC/resolve/main/Hakos%20Baelz/baetalk_e215_s6235.pth",
        "Hakos Baelz RVC v2 (baetalk_e215_s6235.pth)",
    ),
    "bae": (
        BAELZ_PTH,
        "https://huggingface.co/dacoolkid44/VTuber-RVC/resolve/main/Hakos%20Baelz/baetalk_e215_s6235.pth",
        "Hakos Baelz RVC v2 (baetalk_e215_s6235.pth)",
    ),
    "gura": (
        GURA_PTH,
        "https://huggingface.co/dacoolkid44/VTuber-RVC/resolve/main/Gawr%20Gura%20%28Talking%29/GuraTalkV2.pth",
        "Gawr Gura RVC v2 (GuraTalkV2.pth)",
    ),
}

_CONVERTER_CACHE: dict[str, "RVCv2Converter"] = {}


def _ensure_rvc_models(model_path: str, download_url: str):
    os.makedirs(os.path.dirname(CONTENTVEC_ONNX), exist_ok=True)
    if not os.path.exists(CONTENTVEC_ONNX):
        import urllib.request
        print("[RVC] Downloading ContentVec (vec-768-layer-12.onnx)...")
        urllib.request.urlretrieve(
            "https://huggingface.co/ozada/onnx_rvc/resolve/main/vec-768-layer-12.onnx",
            CONTENTVEC_ONNX,
        )
    if not os.path.exists(model_path):
        import urllib.request
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        print(f"[RVC] Downloading {os.path.basename(model_path)}...")
        urllib.request.urlretrieve(download_url, model_path)


def get_rvc_converter(voice_name: str | None = None) -> "RVCv2Converter":
    key = (voice_name or os.getenv("TTS_ENGINE", "baelz")).lower().strip()
    if key not in RVC_VOICE_MODELS:
        key = "baelz"
    default_path, dl_url, _ = RVC_VOICE_MODELS[key]
    m_path = os.getenv("RVC_MODEL_PATH", "").strip() or default_path
    if m_path not in _CONVERTER_CACHE:
        _ensure_rvc_models(m_path, dl_url)
        _CONVERTER_CACHE[m_path] = RVCv2Converter(model_path=m_path)
    return _CONVERTER_CACHE[m_path]


def get_gura_converter():
    return get_rvc_converter("gura")


class RVCv2Converter:
    def __init__(self, model_path: str = GURA_PTH):
        torch.set_num_threads(max(4, (os.cpu_count() or 8) // 2))
        self.device = torch.device("cuda:0" if torch.cuda.is_available() else "cpu")
        self.sr_in = 16000
        self.window = 160

        # 1. Load ContentVec 768-D ONNX with multi-threaded CPU execution
        sess_opts = ort.SessionOptions()
        sess_opts.intra_op_num_threads = max(4, (os.cpu_count() or 8) // 2)
        sess_opts.graph_optimization_level = ort.GraphOptimizationLevel.ORT_ENABLE_ALL
        providers = ["CUDAExecutionProvider", "CPUExecutionProvider"] if torch.cuda.is_available() else ["CPUExecutionProvider"]
        self.hubert_sess = ort.InferenceSession(CONTENTVEC_ONNX, sess_options=sess_opts, providers=providers)

        # 2. Load RVC v2 Generator (GuraTalkV2.pth)
        ckpt = torch.load(model_path, map_location="cpu", weights_only=False)
        self.tgt_sr = int(ckpt["config"][-1])  # 40000 Hz for GuraTalkV2
        ckpt["config"][-3] = ckpt["weight"]["emb_g.weight"].shape[0]
        self.net_g = SynthesizerTrnMs768NSFsid(*ckpt["config"], is_half=False)
        del self.net_g.enc_q
        self.net_g.load_state_dict(ckpt["weight"], strict=False)
        self.net_g.eval().to(self.device).float()

        # Remove weight_norm overhead for faster inference
        for m in self.net_g.modules():
            try:
                torch.nn.utils.remove_weight_norm(m)
            except Exception:
                pass

        self.bh, self.ah = scipy.signal.butter(N=5, Wn=48, btype="high", fs=16000)

        # Warm up CUDA kernels at startup so the very first spoken reply is instant
        if self.device.type == "cuda":
            try:
                with torch.no_grad():
                    dummy_f = torch.zeros((1, 20, 768), device=self.device, dtype=torch.float32)
                    dummy_l = torch.tensor([20], device=self.device, dtype=torch.long)
                    dummy_p = torch.ones((1, 20), device=self.device, dtype=torch.long)
                    dummy_pf = torch.ones((1, 20), device=self.device, dtype=torch.float32) * 200.0
                    dummy_sid = torch.tensor([0], device=self.device, dtype=torch.long)
                    self.net_g.infer(dummy_f, dummy_l, dummy_p, dummy_pf, dummy_sid)
                torch.cuda.synchronize()
            except Exception:
                pass

    def _extract_f0(
        self,
        audio_16k: np.ndarray,
        p_len: int,
        f0_up_key: float = 0.0,
        style_contour: str = "neutral",
    ) -> tuple[np.ndarray, np.ndarray]:
        time_step = (self.window / self.sr_in)
        f0_min, f0_max = 50.0, 800.0
        f0_mel_min = 1127.0 * np.log(1.0 + f0_min / 700.0)
        f0_mel_max = 1127.0 * np.log(1.0 + f0_max / 700.0)

        f0 = (
            parselmouth.Sound(audio_16k.astype(np.float64), self.sr_in)
            .to_pitch_ac(
                time_step=time_step,
                voicing_threshold=0.42,
                pitch_floor=f0_min,
                pitch_ceiling=f0_max,
            )
            .selected_array["frequency"]
        )
        pad_size = (p_len - len(f0) + 1) // 2
        if pad_size > 0 or p_len - len(f0) - pad_size > 0:
            f0 = np.pad(f0, [[max(0, pad_size), max(0, p_len - len(f0) - pad_size)]], mode="constant")
        f0 = f0[:p_len]

        if f0_up_key != 0.0:
            f0 = f0 * (2.0 ** (float(f0_up_key) / 12.0))

        # Add strong VTuber pitch dynamics on voiced frames so RVC never sounds flat/monotone
        voiced = f0 > 0.0
        if np.any(voiced):
            idx_v = np.flatnonzero(voiced)
            prog = np.linspace(0.0, 1.0, len(idx_v), dtype=np.float32)
            # Amplify natural pitch inflection around speaker median (1.65x dynamic range)
            med_f0 = float(np.median(f0[voiced]))
            f0[voiced] = np.clip(med_f0 + (f0[voiced] - med_f0) * 1.65, 75.0, 680.0)
            if style_contour == "question":
                # Pronounced rising question inflection (+3.5 semitones at phrase end)
                semi_curve = -0.6 + 3.8 * (prog ** 2.0) + 0.9 * np.sin(prog * np.pi * 3.0)
            elif style_contour == "excited":
                # High-energy bouncy VTuber pitch contour (+2.8 semitones)
                semi_curve = 1.0 + 2.2 * np.sin(prog * np.pi * 3.5) + 0.8 * np.cos(prog * np.pi * 7.0)
            elif style_contour == "soft":
                # Warm, gentle rise-fall lilt
                semi_curve = 1.4 * np.sin(prog * np.pi) - 0.5
            elif style_contour == "smug":
                # Sassy drop-then-smirk inflection
                semi_curve = -1.4 * np.sin(prog * np.pi * 1.3) + 2.2 * (prog ** 1.8)
            else:
                semi_curve = 1.2 * np.sin(prog * np.pi * 2.5)
            f0[idx_v] = np.clip(f0[idx_v] * (2.0 ** (semi_curve / 12.0)), 75.0, 680.0)

        f0bak = f0.copy().astype(np.float32)
        f0_mel = 1127.0 * np.log(1.0 + f0 / 700.0)
        pos = f0_mel > 0
        f0_mel[pos] = (f0_mel[pos] - f0_mel_min) * 254.0 / (f0_mel_max - f0_mel_min) + 1.0
        f0_mel[f0_mel <= 1] = 1
        f0_mel[f0_mel > 255] = 255
        f0_coarse = np.rint(f0_mel).astype(np.int64)
        return f0_coarse, f0bak

    def convert_to_48k_stereo(
        self,
        samples: np.ndarray,
        sr_in: int,
        f0_up_key: float = 0.0,
        protect: float = 0.33,
        style_contour: str = "neutral",
    ) -> np.ndarray:
        """
        Converts input TTS audio (`samples` at `sr_in` Hz) into Gawr Gura's voice
        and returns a 48,000 Hz stereo float32 array `(N, 2)` for VRChat.
        """
        arr = np.asarray(samples, dtype=np.float32)
        if arr.ndim == 2:
            arr = arr[:, 0]
        arr = arr.ravel()
        if len(arr) < 400:
            return np.zeros((0, 2), dtype=np.float32)

        # Gentle silence trimming only so soft opening/closing consonants (s, f, h, t, k) stay intact
        nz = np.flatnonzero(np.abs(arr) > 0.0015)
        if len(nz) > 0:
            pad_start = int(sr_in * 0.08)
            pad_end = int(sr_in * 0.18)
            arr = arr[max(0, int(nz[0]) - pad_start):min(len(arr), int(nz[-1]) + pad_end)]

        # 1. Resample input to 16,000 Hz for ContentVec & Parselmouth
        if sr_in != 16000:
            n_16k = int(round(len(arr) * 16000.0 / float(sr_in)))
            audio_16k = np.interp(
                np.linspace(0.0, len(arr) - 1, n_16k, dtype=np.float32),
                np.arange(len(arr), dtype=np.float32),
                arr,
            ).astype(np.float32)
        else:
            audio_16k = arr

        audio_16k = scipy.signal.filtfilt(self.bh, self.ah, audio_16k).astype(np.float32)
        max_amp = float(np.max(np.abs(audio_16k)))
        if max_amp > 1.0:
            audio_16k /= max_amp

        orig_16k_len = len(audio_16k)
        expected_tgt_len = int(round(orig_16k_len * float(self.tgt_sr) / 16000.0))

        # Constant zero silence pad: 400ms right padding absorbs ContentVec 7-layer CNN frame drop
        # without ever truncating the final syllable of speech!
        t_pad_left = int(16000 * 0.15)
        t_pad_right = int(16000 * 0.40)
        t_pad_left_tgt = int(round(t_pad_left * float(self.tgt_sr) / 16000.0))
        extra_tail_tgt = int(round(0.10 * float(self.tgt_sr)))
        audio_pad = np.pad(audio_16k, (t_pad_left, t_pad_right), mode="constant", constant_values=0.0).astype(np.float32)
        p_len = len(audio_pad) // self.window

        # 2. Extract F0 Pitch Contour
        f0_coarse, f0_float = self._extract_f0(
            audio_pad, p_len, f0_up_key=f0_up_key, style_contour=style_contour
        )

        # 3. Extract 768-D ContentVec features via ONNX
        ort_in = audio_pad.reshape(1, 1, -1)
        feats_np = self.hubert_sess.run(["embed"], {"source": ort_in})[0]
        feats = torch.from_numpy(feats_np).to(self.device).float()
        feats0 = feats.clone()

        # 4. Interpolate 2x time steps & apply consonant protection
        feats = F.interpolate(feats.permute(0, 2, 1), scale_factor=2).permute(0, 2, 1)
        feats0 = F.interpolate(feats0.permute(0, 2, 1), scale_factor=2).permute(0, 2, 1)

        if feats.shape[1] < p_len:
            p_len = feats.shape[1]
            f0_coarse = f0_coarse[:p_len]
            f0_float = f0_float[:p_len]

        pitch = torch.from_numpy(f0_coarse[:p_len]).unsqueeze(0).to(self.device).long()
        pitchf = torch.from_numpy(f0_float[:p_len]).unsqueeze(0).to(self.device).float()

        if protect < 0.5:
            pitchff = pitchf.clone()
            pitchff[pitchf > 0] = 1.0
            pitchff[pitchf < 1] = float(protect)
            pitchff = pitchff.unsqueeze(-1)
            feats = feats * pitchff + feats0[:, :p_len, :] * (1.0 - pitchff)

        p_len_t = torch.tensor([p_len], device=self.device).long()
        sid = torch.tensor([0], device=self.device).long()

        # 5. Run RVC v2 NSF-HiFiGAN Generator
        with torch.no_grad():
            audio_out = self.net_g.infer(feats[:, :p_len, :], p_len_t, pitch, pitchf, sid)[0][0, 0].cpu().numpy()

        start_idx = min(t_pad_left_tgt, max(0, len(audio_out) - 1))
        end_idx = min(len(audio_out), start_idx + expected_tgt_len + extra_tail_tgt)
        audio_out = audio_out[start_idx:end_idx]

        # Gentle 10 ms edge fade to prevent clicks
        fade = min(int(self.tgt_sr * 0.01), len(audio_out) // 4)
        if fade > 0:
            ramp = np.linspace(0.0, 1.0, fade, dtype=np.float32)
            audio_out = audio_out.copy()
            audio_out[:fade] *= ramp
            audio_out[-fade:] *= ramp[::-1]

        # 6. Resample from 40,000 Hz -> 48,000 Hz stereo for VRChat
        n_48k = max(1, int(round(len(audio_out) * 48000.0 / float(self.tgt_sr))))
        s48 = np.interp(
            np.linspace(0.0, len(audio_out) - 1, n_48k, dtype=np.float32),
            np.arange(len(audio_out), dtype=np.float32),
            audio_out,
        ).astype(np.float32)

        # Consistent RMS loudness + soft-knee limiting so quiet words never drop below VRChat uSpeak VAD
        active = s48[np.abs(s48) > 0.015]
        if len(active) > 0:
            rms = float(np.sqrt(np.mean(active * active)))
            if rms > 0.005:
                gain = min(4.5, 0.20 / rms)
                s48 = s48 * gain
        s48 = np.tanh(s48 * 0.95).astype(np.float32)
        peak = float(np.max(np.abs(s48)))
        if peak > 0.01:
            s48 = (s48 / peak) * 0.88

        # Prepend 180ms & append 450ms zero silence so VB-Cable and VRChat uSpeak never clip start/tail
        lead_sil = np.zeros(int(48000 * 0.18), dtype=np.float32)
        tail_sil = np.zeros(int(48000 * 0.45), dtype=np.float32)
        s48 = np.concatenate((lead_sil, s48, tail_sil))

        return np.column_stack((s48, s48))
