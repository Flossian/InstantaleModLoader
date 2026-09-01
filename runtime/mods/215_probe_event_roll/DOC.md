# `215_probe_event_roll`

クエスト中のミニイベントの成否判定を写す。LLM が返した説得力(credibility)と参照能力値、ゲームが書き込んだ確率、判定結果、そのときの能力値・HP・体力・負傷を1件ずつ突き合わせる。`Character.calculate_attribute` が判定の窓の間に呼ばれるかも、呼び出し元ごと記録する
