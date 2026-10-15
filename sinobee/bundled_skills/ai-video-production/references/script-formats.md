# Phase 1 - Script formats and audio-first timing

Write `script.body` (markdown) in the format of the genre, then derive `shots`. The script is what the user
approves; the shots are what gets rendered.

## Film / commercial - screenplay
```
INT. 夜市小吃攤 - NIGHT

阿珍（30 多歲，圍裙沾著麵粉）快速翻動鍋裡的蚵仔煎。蒸氣撲上她的眼鏡。

            阿珍
      （頭也不抬）
   再等一下，馬上好！

一名小男孩踮腳看著鍋子，吞了吞口水。
```
Scene heading (INT./EXT., place, time) -> one `environment`. Action lines -> shot `action`. Parentheticals ->
`performance`. Dialogue -> `dialogue` (spoken on camera; H3 generates the voice and lip movement).

## Documentary / explainer - two columns
| 旁白 / 訪談 (audio) | 畫面 / B-roll / 證據提示 (picture) |
|---|---|
| 清晨五點，阿明已經在茶園裡。 | 薄霧中的梯田茶園全景，阿明在遠處採茶 |
| 「這片茶，我顧了四十年。」(訪談) | 阿明中景，坐在茶寮前，看向鏡頭左側 |
| 1980 年代，凍頂烏龍外銷量達到高峰。 | 史料提示：舊茶箱、黑白照片質感的茶廠（標註「重現」） |

Left column -> `narration` (voice-over) or `dialogue` (an interview line said on camera). Right column -> picture
fields. Claims in narration must be true: research them first, or keep them general.

## Vlog / music / personal record - timeline
```
00:00 起床，窗外下雨（環境音：雨聲） - 旁白：「其實今天本來想去爬山…」
00:05 煮咖啡，手沖壺特寫（音效：水聲）
00:12 改去附近的書店，步行跟拍
00:20 結尾：窗邊看書，雨停了 - 旁白：「就…這樣也不錯。」
```
Each line -> a shot; environment sounds -> `sfx`; personal thoughts -> `narration` in the subject's
linguistic blueprint (his habitual words, sentence length).

## From script to shots
- 2-6 s per shot; documentaries lean longer (4-6 s), music and ads shorter (1.5-3 s).
- A `group` = consecutive shots in one location and lighting, <= 15 s total. H3 renders the group as one clip with
  real cuts at the times you give. Fewer groups = faster, more coherent audio; a new group = a clean break
  (new place, time jump, or a shot that must start from a keyframe).
- Total length: sum of shots. 30 s is usually 6-10 shots in 3-5 groups.

## Audio-first timing
1. Narration/dialogue text -> audio: `tts.py <project>` (edge-tts neural voices sound best; install the Python
   package `edge-tts` if missing) or the user's own recordings set as `audio_file` with `audio_role: recorded`.
2. `project.py timing <project>` measures each file and sets `duration` = audio + 0.2 s, rounded up to 0.5 s.
3. Narration longer than ~6 s: split the sentence over two shots (B-roll changes mid-sentence feel natural).
4. Shots without speech keep the duration you chose (2-4 s for B-roll, 1-2 s for inserts).
5. Dialogue gets a "guide" track: it only locks timing. The final voice comes from H3 on camera.
