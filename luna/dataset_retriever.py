"""
Zero-VRAM Hugging Face Dataset Retriever & Persistent Long-Term Memory for Luna.

- Loads `luna/datasets/sassy_neuro_dataset.jsonl` (749 rows from Hugging Face
  `neifuisan/Neuro-sama-QnA` + `Braite/neuro.alpha` + curated Sassy/Smug VRChat banter).
- Retrieves the top matching sassy/smug style examples in ~1 ms on CPU (0.0 GB GPU VRAM).
- Maintains a lightweight persistent episodic memory (`luna/datasets/long_term_memory.json`)
  so Luna remembers facts about Kai and VRChat friends across sessions.
"""
import json
import math
import random
import re
import time
from collections import Counter
from pathlib import Path

DATASET_PATH = Path(__file__).resolve().parent / "datasets" / "sassy_neuro_dataset.jsonl"
MEMORY_PATH = Path(__file__).resolve().parent / "datasets" / "long_term_memory.json"

_STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "were", "be", "to", "of", "in",
    "on", "at", "for", "with", "and", "or", "but", "so", "it", "this",
    "that", "i", "me", "my", "we", "us", "he", "she", "they", "them",
    "do", "does", "did", "can", "could", "would", "should", "just", "now",
    "right", "hey", "hi", "oh", "luna",
}

_WORD_RE = re.compile(r"[a-z0-9']+")


def _tokenize(text: str) -> list[str]:
    return [w for w in _WORD_RE.findall(text.lower()) if w not in _STOPWORDS and len(w) > 1]


class SassyDatasetRetriever:
    """Fast CPU-only BM25/TF-IDF style matcher over the Hugging Face Neuro-sama dataset."""

    def __init__(self, path: Path = DATASET_PATH):
        self.path = path
        self.rows: list[dict] = []
        self.curated_rows: list[dict] = []
        self.idf: dict[str, float] = {}
        self._load()

    def _load(self):
        if not self.path.exists():
            try:
                from luna.datasets.build_dataset import build_dataset
                build_dataset()
            except Exception:
                return
        if not self.path.exists():
            return

        df: Counter[str] = Counter()
        with open(self.path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                    tokens = set(_tokenize(rec["instruction"] + " " + rec["output"]))
                    q_tokens = set(_tokenize(rec["instruction"]))
                    rec["_tokens"] = tokens
                    rec["_q_tokens"] = q_tokens
                    self.rows.append(rec)
                    if rec.get("source") == "curated_sassy_smug":
                        self.curated_rows.append(rec)
                    for t in tokens:
                        df[t] += 1
                except Exception:
                    continue

        n = max(1, len(self.rows))
        self.idf = {t: math.log(1.0 + (n - freq + 0.5) / (freq + 0.5)) for t, freq in df.items()}

    def retrieve_examples(self, query: str, top_k: int = 4, random_anchors: int = 2) -> list[dict]:
        """Returns top_k matching rows plus `random_anchors` sassy anchor rows."""
        if not self.rows:
            return []
        q_tokens = set(_tokenize(query))
        scored: list[tuple[float, dict]] = []

        if q_tokens:
            for rec in self.rows:
                overlap_q = q_tokens & rec["_q_tokens"]
                overlap_all = q_tokens & rec["_tokens"]
                if not overlap_all:
                    continue
                idf_q = sum(self.idf.get(t, 1.0) for t in overlap_q)
                idf_all = sum(self.idf.get(t, 1.0) for t in overlap_all)
                score = (idf_q * 2.2 + idf_all * 0.8) * float(rec.get("sass_weight", 1.0))
                scored.append((score, rec))

        scored.sort(key=lambda x: x[0], reverse=True)
        picked = [r for _, r in scored[:top_k]]
        picked_keys = {r["instruction"] for r in picked}

        pool = [r for r in (self.curated_rows or self.rows) if r["instruction"] not in picked_keys]
        if pool and random_anchors > 0:
            picked.extend(random.sample(pool, min(random_anchors, len(pool))))

        return picked

    def format_prompt_block(self, query: str) -> str:
        """Builds a compact few-shot style block injected into the system prompt."""
        examples = self.retrieve_examples(query, top_k=2, random_anchors=1)
        if not examples:
            return ""
        lines = ["[PLAYFUL & WITTY STYLE EXAMPLES (keep it lighthearted, fun, and friendly, never mean):]"]
        for ex in examples:
            act = ex.get("action", "stare_smug")
            lines.append(f"- User: \"{ex['instruction']}\" -> Luna: \"[action:{act}] {ex['output']}\"")
        return "\n".join(lines)


class PersistentMemory:
    """Lightweight JSON-backed episodic memory for facts about Kai & VRChat players."""

    _FACT_PATTERNS = re.compile(
        r"\b(my name is|call me|i'm |i am |i love|i hate|i like|i play|my favorite|my friend|remember that|don't forget|i am studying|i work on|i'm from|this is my friend)\b",
        re.IGNORECASE,
    )

    _IGNORE_FRIEND_NAMES = {
        "kai", "system", "vrchat friend", "another vrchat player",
        "vrchat friend (chatbox)", "dumalcoholicpa3", "kairu!", "kairuǃ", "luna",
    }

    def __init__(self, path: Path = MEMORY_PATH):
        self.path = path
        self.facts: list[dict] = []
        self._load()

    def _load(self):
        if self.path.exists():
            try:
                self.facts = json.loads(self.path.read_text(encoding="utf-8"))
            except Exception:
                self.facts = []

    def _save(self):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            self.path.write_text(json.dumps(self.facts[-80:], indent=2, ensure_ascii=False), encoding="utf-8")
        except Exception:
            pass

    def remember_friend(self, name: str, world: str = "", note: str = ""):
        """Persistently remembers a VRChat friend's name (and where/what we chatted about)."""
        clean = (name or "").strip()
        if not clean or clean.lower() in self._IGNORE_FRIEND_NAMES:
            return
        if clean.lower().startswith(("other player", "nearby player")):
            return
        detail = f"met in {world}" if world else "VRChat friend of Kai"
        if note:
            detail = f"{detail}; {note[:90]}"
        entry = f"Friend {clean}: {detail}"
        # Update existing friend entry or append new one
        for f in self.facts:
            if f.get("friend", "").lower() == clean.lower():
                if note:
                    f["fact"] = entry
                f["ts"] = int(time.time())
                self._save()
                return
        self.facts.append({"user": clean, "friend": clean, "fact": entry, "ts": int(time.time())})
        self._save()

    def maybe_remember(self, user: str, message: str):
        """Automatically saves notable personal facts or explicit 'remember' statements."""
        if not message or user == "System" or message.startswith("[System:"):
            return
        clean = message.strip()
        if len(clean) < 5 or len(clean) > 180:
            return
        if self._FACT_PATTERNS.search(clean):
            entry = f"{user}: {clean}"
            if not any(f.get("fact", "").lower() == entry.lower() for f in self.facts):
                self.facts.append({"user": user, "fact": entry, "ts": int(time.time())})
                self._save()

    def get_context_block(self, query: str = "", limit: int = 5) -> str:
        if not self.facts:
            return ""
        q_tokens = set(_tokenize(query))
        scored = []
        for idx, item in enumerate(self.facts):
            f_tokens = set(_tokenize(item.get("fact", "")))
            overlap = len(q_tokens & f_tokens)
            # Score by keyword overlap + recency
            scored.append((overlap * 3.0 + (idx * 0.05), item["fact"]))
        scored.sort(key=lambda x: x[0], reverse=True)
        chosen = [fact for _, fact in scored[:limit]]
        return "[LONG-TERM MEMORY ABOUT KAI & FRIENDS: " + " | ".join(chosen) + "]"
