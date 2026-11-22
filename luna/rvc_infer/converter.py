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

_CONVERTER_SINGLETON = None


def _ensure_rvc_models(model_path: str):
    os.makedirs(os.path.dirname(CONTENTVEC_ONNX), exist_ok=True)
    if not os.path.exists(CONTENTVEC_ONNX):
        import urllib.request
        print("[Gura RVC] Downloading ContentVec (vec-768-layer-12.onnx)...")
        urllib.request.urlretrieve(
            "https://huggingface.co/ozada/onnx_rvc/resolve/main/vec-768-layer-12.onnx",
            CONTENTVEC_ONNX,
        )
    if not os.path.exists(model_path):
        import urllib.request
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        print("[Gura RVC] Downloading Gawr Gura RVC v2 (GuraTalkV2.pth)...")
        urllib.request.urlretrieve(
            "https://huggingface.co/dacoolkid44/VTuber-RVC/resolve/main/Gawr%20Gura%20%28Talking%29/GuraTalkV2.pth",
            model_path,
        )


def get_gura_converter():
    global _CONVERTER_SINGLETON
    if _CONVERTER_SINGLETON is None:
        m_path = os.getenv("RVC_MODEL_PATH", GURA_PTH)
        _ensure_rvc_models(m_path)
        _CONVERTER_SINGLETON = RVCv2Converter(model_path=m_path)
    return _CONVERTER_SINGLETON


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

        # Remove weight_norm overhead for faster CPU inference
        for m in self.net_g.modules():
            try:
                torch.nn.utils.remove_weight_norm(m)
            except Exception:
                pass

        self.bh, self.ah = scipy.signal.butter(N=5, Wn=48, btype="high", fs=16000)

    def _extract_f0(self, audio_16k: np.ndarray, p_len: int, f0_up_key: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
        time_step = (self.window / self.sr_in)
        f0_min, f0_max = 65.0, 800.0
        f0_mel_min = 1127.0 * np.log(1.0 + f0_min / 700.0)
        f0_mel_max = 1127.0 * np.log(1.0 + f0_max / 700.0)

        f0 = (
            parselmouth.Sound(audio_16k.astype(np.float64), self.sr_in)
            .to_pitch_ac(
                time_step=time_step,
                voicing_threshold=0.6,
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

        # Trim leading/trailing silence before RVC so we only convert active speech!
        nz = np.flatnonzero(np.abs(arr) > 0.008)
        if len(nz) > 0:
            pad_in = int(sr_in * 0.04)
            arr = arr[max(0, int(nz[0]) - pad_in):min(len(arr), int(nz[-1]) + pad_in)]

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

        # Minimal 0.06s reflection pad
        t_pad = int(16000 * 0.06)
        t_pad_tgt = int(self.tgt_sr * 0.06)
        audio_pad = np.pad(audio_16k, (t_pad, t_pad), mode="reflect").astype(np.float32)
        p_len = len(audio_pad) // self.window

        # 2. Extract F0 Pitch Contour
        f0_coarse, f0_float = self._extract_f0(audio_pad, p_len, f0_up_key=f0_up_key)

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

        if len(audio_out) > 2 * t_pad_tgt:
            audio_out = audio_out[t_pad_tgt:-t_pad_tgt]

        # 6. Resample from 40,000 Hz -> 48,000 Hz stereo for VRChat
        n_48k = int(round(len(audio_out) * 48000.0 / float(self.tgt_sr)))
        s48 = np.interp(
            np.linspace(0.0, len(audio_out) - 1, n_48k, dtype=np.float32),
            np.arange(len(audio_out), dtype=np.float32),
            audio_out,
        ).astype(np.float32)

        peak = float(np.max(np.abs(s48)))
        if peak > 0.01:
            s48 = (s48 / peak) * 0.88

        return np.column_stack((s48, s48))
