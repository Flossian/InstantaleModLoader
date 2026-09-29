# `232_probe_facility_choices`

宿屋だけ `出る` が先頭に並ぶ原因を測る。施設へ入るたびに、`choices` の型と並び、組み終えた `app.buttons` の並び、ハッシュの設定を `out\facility_choices.log` に残す。決着（起動2回）。`choices` は dict で、宿屋の `宿泊する` は `choices` の外から後ろに足されるのが原因で、ハッシュは無関係だった（GAME.md §2.2。直しは `135_fix_inn_button_order`）
