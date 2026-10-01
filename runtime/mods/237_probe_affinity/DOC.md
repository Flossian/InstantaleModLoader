# `237_probe_affinity`

NPC のプレイヤーへの好感度（`relationship.player.affinity`）がいつ・どれだけ動くかを録る。依頼のクリア・依頼の放棄・日数の送り・会話の終わりの前後で好感度を比べ（依頼と会話の終わりは15秒後にもう一度）、動いた人と同行者を残す（`kind: change` / `still` / `late`）。感情の文を組み直す `document_emotion_scores_new` の引数・戻り・呼び出し元も録る（`kind: emotion`）。会話で好感度が動く MOD を作るかを決める下調べ。出力は `out\affinity.log` / `out\affinity.jsonl`
