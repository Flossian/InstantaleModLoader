# `400_Gemini_user_role_Fix`: クラウドAPI経由でGemini使用時のユーザーロール補完

    クラウドAPIでのGemini経路は、system roleだけになっているLLMリクエスト
    に対し返答ができず、裏でクラッシュする。
    このMODでは現状で確認できている system roleのみのリクエスト3件に対し、
    必要最小限のダミーとなる user roleを補う。
    GPT系は system roleのみでも返答が可能な為、素通しする。
    ローカルLLMは検証できていない為、これも素通しする。

## 対象

    - クラウドLLMのGemini系統
    - 現時点で確認が取れている、user role が欠落している3件のリクエスト

## 補完対象のmanagerと追加するuser roleの文章

| 追加するmanager | 追加するuserの文章 |
| --- | --- |
| `shop_item_generator_ordinary` | `＜売買する＞` |
| `epilogue_pre_evaluator` | `＜エピローグを評価する＞` |
| `epilogue_generator` | `＜エピローグを生成する＞` |

## 実装の方針

共有部品の`llm.watch_aliases`で後生えの送信関数を
監視し、`ctx.wrap`で最小限の引数差し替えのみを行う。
system role の原文には触れない。
ゲーム本体・ModLoader・セーブデータ・他MODに干渉させない。
公式で修正されたら降ろしてよい。
