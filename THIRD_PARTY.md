# Third-party sources and model licenses

This application coordinates separately installed components. Their licenses and model terms remain applicable; repository availability is not permission for every use. The application code's own license has not yet been selected by the owner. Do not treat this document as a license grant.

| Component | Source and license reference | Relationship to this project |
|---|---|---|
| ComfyUI | [Official source](https://github.com/Comfy-Org/ComfyUI) · [GPL-3.0 license at the pinned revision](https://github.com/Comfy-Org/ComfyUI/blob/f14bbe28697778b7c2427d4b71c7fac24b78f8f4/LICENSE) | Downloaded into `.runtime/`; native model code stays upstream. Workflow graphs are based on its official YuE2 blueprint. |
| YuE2 INT8 weights | [Comfy-Org repack](https://huggingface.co/Comfy-Org/YuE2) · [original YuE2-3B](https://huggingface.co/m-a-p/YuE2-3B) · [CC BY-NC 4.0](https://creativecommons.org/licenses/by-nc/4.0/) | Downloaded separately and checksum-verified. The noncommercial model license must be considered before monetized streaming. This app does not establish commercial permission. |
| YuE reference project | [Official repository](https://github.com/multimodal-art-projection/YuE) | Reference documentation for controls and model behavior. The bundled runtime uses ComfyUI. |
| Qwen3.5-4B | [Official model card, Apache 2.0](https://huggingface.co/Qwen/Qwen3.5-4B) · [Ollama distribution](https://ollama.com/library/qwen3.5:4b) | Downloaded by Ollama; local CPU lyric generation. |
| Ollama | [Source / MIT license](https://github.com/ollama/ollama/blob/main/LICENSE) | Separately installed local model service. Model weights have their own terms. |
| FFmpeg | [Official license information](https://ffmpeg.org/legal.html) | External media tool. Licensing depends on build options; this project requires libx264 support. |
| Python packages and PyTorch | [Studio lock](uv.lock) · [music requirements](workflows/music-runtime-requirements.lock.txt) · [PyTorch source](https://github.com/pytorch/pytorch) | Installed dependencies retain their package licenses and notices. |
| OpenAI API | [API documentation](https://developers.openai.com/api/docs) · [service terms](https://openai.com/policies/service-terms/) | Optional paid hosted artwork service; no OpenAI model weights are distributed. |

Keep upstream license and attribution files when distributing downloaded components. This repository excludes model weights, dependency environments, and personal media. Before distributing a runtime bundle rather than just this source repository, review the licenses of everything included in that bundle.
