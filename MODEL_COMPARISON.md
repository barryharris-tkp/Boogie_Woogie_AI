# Country/pop model selection for this PC

**Checked September 14, 2026 (America/Chicago).** Primary sources only: model authors' repositories/model cards, published evaluation protocol, and the author of the cited C++ runtime. No additional model was installed for this comparison.

## Provisional choice

**Keep the installed Comfy-Org YuE2 INT8 checkpoint with melody/chord planning as the working default.** It has completed a real country/pop request on this RTX 4080 SUPER: 61.96 seconds of stereo audio in 16.97 seconds, with a sampled total GPU-memory peak of 8,707 MiB. That establishes feasibility, not that it sounds better than every alternative. [Local evidence](RUNTIME_VERIFICATION.md)

**Benchmark ACE-Step 1.5 XL Turbo next**, particularly if the station will earn revenue. Compare XL SFT for quality and standard 2B Turbo for extra memory/speed headroom. Use YuE2 BF16 as the precision comparison before deciding whether its extra memory use buys an audible improvement.

## Practical shortlist

| Candidate | Fit on 16 GB / operating tradeoff | Quality interpretation | Weight license |
|---|---|---|---|
| **YuE2 INT8 + full planning — installed** | Proven here for a short original song; leaves useful headroom. ComfyUI packages the original model rather than training a distinct edition. | Strong candidate for complete vocal songs and editable melody/chords. INT8 listening parity with BF16 has not been established. | CC-BY-NC-4.0. [Comfy model card](https://huggingface.co/Comfy-Org/YuE2) |
| **YuE2 BF16 + full planning** | 7.80 GB checkpoint versus 3.96 GB INT8; these are file sizes, not runtime peaks. Official unquantized setup recommends a 24 GB GPU. Local BF16 fit/speed remain untested. | Reference for whether quantization affects this user's country/pop output; higher precision alone does not prove an audible gain. | CC-BY-NC-4.0. [Files](https://huggingface.co/Comfy-Org/YuE2/tree/main/checkpoints), [official runtime requirements](https://huggingface.co/m-a-p/YuE2-3B#-speed-and-resources) |
| **ACE-Step 1.5 XL Turbo** | 8-step distilled model; official card supports 16 GB with CPU offload, recommends 20 GB without. Begin with the 1.7B planning LM and one song at a time. | Best next operational candidate. Author claims of high quality are not a country/pop listening comparison on this PC. | MIT; card explicitly permits commercial generated music. [XL Turbo card](https://huggingface.co/ACE-Step/acestep-v15-xl-turbo) |
| **ACE-Step 1.5 XL SFT** | 50 steps with classifier-free guidance; same offload requirements. Expect more denoising work, but benchmark actual end-to-end time. | Authors designate this their highest-quality variant. Test whether that improves vocals/arrangement enough to justify its cost. | MIT. [XL SFT card](https://huggingface.co/ACE-Step/acestep-v15-xl-sft) |
| **MiniMax Music 3** | Official full-precision path targets about 24 GB; layer offload can fit smaller cards but slows generation. A separate C++ implementation reports about 9 GB for quantized stage-swapped operation. Neither path tested here. | Worth comparing expressive vocals and section-level arrangements; no evidence here that it beats YuE2 for country. | Custom MiniMax-Music3 Community License; see conditions below. [Official model](https://huggingface.co/MiniMaxAI/MiniMax-Music3), [runtime-author memory report](https://github.com/ServeurpersoCom/minimaxmusic.cpp) |

ACE-Step's current GPU guide recommends offload and INT8 on its 16–20 GB tier; it calls 12–16 GB marginal for XL. A nominal 16 GB desktop GPU with other applications using memory deserves the more conservative configuration. Its lightweight 2B Turbo remains a useful fallback; the general project's sub-10-second RTX 3090 claim must not be applied to XL or this PC without testing. [GPU guide](https://github.com/ace-step/ACE-Step-1.5/blob/main/docs/en/GPU_COMPATIBILITY.md), [official model zoo](https://github.com/ace-step/ACE-Step-1.5#-model-zoo)

**Additional candidate:** HeartMuLa's authors currently recommend `HeartMuLa-oss-3B-happy-new-year` with `HeartCodec-oss-20260123` (Apache 2.0) and support lazy loading to reduce single-GPU memory use. It merits a listening comparison if the shortlist disappoints; an exact 4080 SUPER throughput/VRAM figure is unverified. Their internal 7B quality claims should not be attributed to the released 3B model. [Recommended release](https://huggingface.co/HeartMuLa/HeartMuLa-oss-3B-happy-new-year), [official runtime](https://github.com/HeartMuLa/heartlib)

## What published quality scores establish

YuE2's authors report SongBench averages of 6.7316 for YuE2, 6.2830 for MiniMax Music 3, and 6.0118 for the entry named ACE-Step 1.5. However, standard YuE2 already selects one of **two** candidates; its 6.9632 headline selects one of **eight**, partly using a SongBench dimension. The 192-prompt comparison uses automatic evaluators, different systems lead other metrics, and compute is not matched. Its ACE-Step label does not identify an XL Turbo/SFT configuration. It therefore cannot establish that our single INT8 output beats current ACE-Step XL, or that one model wins specifically for country music. [Benchmark protocol](https://github.com/multimodal-art-projection/YuE/blob/main/docs/benchmarks.md)

The benchmark also uses `YuE2-Vae-legacy`; the authors recommend `YuE2-Vae` for listening. Our Comfy checkpoint embeds its decoder and was not validated as a reproduction of that benchmark pipeline. Keep decoder, precision, sampler, and candidate count recorded when comparing. [Decoder guidance](https://huggingface.co/m-a-p/YuE2-Vae)

## Public streaming and model choice

YuE2's noncommercial weight license leaves monetized production unresolved. ACE-Step is the first alternative to evaluate for that scenario. MiniMax Music3's current custom license includes commercial UI attribution, separate authorization above $20 million aggregate annual relevant revenue, conditions for hosted generation services, and disclosure of AI-generated public content. It is not interchangeable with MIT; review the exact terms against the eventual station features. [YuE2 license declaration](https://huggingface.co/m-a-p/YuE2-3B), [MiniMax license](https://huggingface.co/MiniMaxAI/MiniMax-Music3/blob/main/LICENSE)

## Decision test still needed

Use the same original lyrics across classic country, modern country-pop, acoustic pop, and polished pop; include male/female vocals and several seeds. Hide model names during listening. Score vocal naturalness, words sung correctly, melody, instrumental realism, mix, repetition, and endings. Measure **accepted audio minutes per elapsed hour**, including lyric writing, all candidates, screening, and packaging while the broadcast encoder is running. Cache accepted songs for replay.

The winner should satisfy both the listening preference and sustained queue supply. Current evidence supports retaining YuE2 INT8 for development and evaluating ACE-Step XL next; it does not yet support declaring a universal best model or unattended 24/7 readiness.
