# `230_probe_image_generation`

画像生成のバックエンドと出口を録る。画像の強化を DLL の差し替えからローダの MOD へ移すための下調べで、(1) 選ばれた方式だけが import されるか、(2) 生成の出口は1つか、(3) チェックポイント・TAESD・VAE がいつ決まり、設定を変えたら組み直されるか、を測る。上の層（アニメ・写実・img2img）で印を立て、下の出口で引数を残す。方式は名指しせず、`sys.modules` と import の瞬間の観測者から引く。出力は `out\image_generation.log` / `out\image_generation.jsonl`。読み取りは決着（GAME.md §2.33、VERIFICATION_LOG.md §2.86）、書き込み側の手順は VERIFICATION.md §3.65
