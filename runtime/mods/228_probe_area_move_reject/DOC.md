# `228_probe_area_move_reject`

エリア移動の拒否（`area_move_rejector`）が、同行者の何を読んで決めているかを録る。`AreaMoveManager.execute` の窓の間だけ、同行者の `Character`・`relationship`・名簿を読みを記録する写しに差し替え、読まれた順に `out\area_move_reject.log` へ残す（窓を閉じたら元へ戻し、セーブには書かない）。`329_` の当て推量が2度外れた後の材料で、決着（GAME.md §2.18 / VERIFICATION.md §3.56）
