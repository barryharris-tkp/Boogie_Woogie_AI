"""Completion metadata for the pinned ComfyUI YuE2 implementation.

Only public CLIP methods are used; sampling remains in ComfyUI. Keep imports
stdlib-only so the reporting contract can be tested without torch or a GPU.
"""

import json


FRAMES_PER_SECOND = 25


class BoogieYuE2GenerateABC:
    """Mirror native ABC generation and retain its exact budget-stop signal."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "clip": ("CLIP",),
            "style": ("STRING", {"multiline": True, "dynamicPrompts": True}),
            "lyrics": ("STRING", {"multiline": True, "dynamicPrompts": True}),
            "seed": ("INT", {"default": 0, "min": 0, "max": 0xffffffffffffffff}),
            "mode": (["full", "melody"],),
            "max_abc_tokens": ("INT", {"default": 8192, "min": 1, "max": 20000}),
            "temperature": ("FLOAT", {"default": 0.7, "min": 0.0, "max": 5.0, "step": 0.05}),
            "top_p": ("FLOAT", {"default": 0.9, "min": 0.01, "max": 1.0, "step": 0.01}),
            "top_k": ("INT", {"default": 30, "min": 1, "max": 32768}),
            "repetition_penalty": ("FLOAT", {"default": 1.005, "min": 0.01, "max": 10.0, "step": 0.005}),
            "penalty_window": ("INT", {"default": 100, "min": 1, "max": 20000}),
        }}

    RETURN_TYPES = ("STRING", "BOOLEAN")
    RETURN_NAMES = ("abc", "abc_truncated")
    FUNCTION = "generate_abc"
    CATEGORY = "Boogie Woogie/YuE2"

    def generate_abc(self, clip, style, lyrics, seed, mode, max_abc_tokens,
                     temperature=0.7, top_p=0.9, top_k=30,
                     repetition_penalty=1.005, penalty_window=100):
        if type(max_abc_tokens) is not int or not 1 <= max_abc_tokens <= 20000:
            raise ValueError("YuE2 ABC token budget must be an integer from 1 to 20000")
        # Identical public calls and kwargs to pinned YuE2GenerateABC.execute.
        tokens = clip.tokenize(style, lyrics=lyrics, cot=mode, seed=seed,
                               max_tokens=max_abc_tokens, penalty_window=penalty_window)
        ids = clip.generate(tokens, max_length=max_abc_tokens, temperature=temperature,
                            top_p=top_p, top_k=top_k,
                            repetition_penalty=repetition_penalty, seed=seed)
        # Native _generate excludes the end token. At the budget it returns
        # exactly this many IDs; decode/re-tokenize can change that count.
        abc_truncated = len(ids) >= max_abc_tokens
        abc = clip.decode(ids)
        if not isinstance(abc, str):
            raise ValueError("YuE2 ABC decoder did not return text")
        return (abc, abc_truncated)


class BoogieYuE2Report:
    """Expose exact semantic completion and the score used for this song."""

    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "conditioning": ("CONDITIONING",),
            "clip": ("CLIP",),
            "planning_generated": ("BOOLEAN", {"default": False}),
            "abc_truncated": ("BOOLEAN", {"default": False}),
        }}

    RETURN_TYPES = ()
    FUNCTION = "report"
    OUTPUT_NODE = True
    CATEGORY = "Boogie Woogie/YuE2"

    def report(self, conditioning, clip, planning_generated, abc_truncated):
        if type(planning_generated) is not bool or type(abc_truncated) is not bool:
            raise ValueError("YuE2 planning completion inputs must be booleans")
        if not planning_generated and abc_truncated:
            raise ValueError("A supplied or absent ABC score cannot have a generated truncation flag")
        if (not isinstance(conditioning, (list, tuple)) or len(conditioning) != 1
                or not isinstance(conditioning[0], (list, tuple))
                or len(conditioning[0]) != 2 or not isinstance(conditioning[0][1], dict)):
            raise ValueError("Expected exactly one native YuE2 conditioning entry")
        metadata = conditioning[0][1]
        semantic_truncated = metadata.get("yue2_truncated")
        frames = metadata.get("yue2_frames")
        abc_ids = metadata.get("yue2_abc_ids")
        if type(semantic_truncated) is not bool:
            raise ValueError("Missing or invalid native YuE2 truncation metadata")
        if type(frames) is not int or frames < 1:
            raise ValueError("Missing or invalid native YuE2 frame count")
        if (not isinstance(abc_ids, (list, tuple))
                or any(type(token) is not int or token < 0 for token in abc_ids)):
            raise ValueError("Missing or invalid native YuE2 ABC token IDs")
        if planning_generated and not abc_ids:
            raise ValueError("Generated YuE2 plan is absent from music conditioning")
        abc = clip.decode(abc_ids)
        if not isinstance(abc, str):
            raise ValueError("YuE2 ABC decoder did not return text")
        result = {
            "schema_version": 1,
            "semantic_truncated": semantic_truncated,
            "frames": frames,
            "seconds": frames / FRAMES_PER_SECOND,
            "abc": abc,
            "abc_truncated": abc_truncated,
        }
        return {"ui": {"text": [json.dumps(result, ensure_ascii=False, allow_nan=False)]}}


NODE_CLASS_MAPPINGS = {
    "BoogieYuE2GenerateABC": BoogieYuE2GenerateABC,
    "BoogieYuE2Report": BoogieYuE2Report,
}
NODE_DISPLAY_NAME_MAPPINGS = {
    "BoogieYuE2GenerateABC": "YuE2 Generate ABC with Completion",
    "BoogieYuE2Report": "YuE2 Completion Report",
}
