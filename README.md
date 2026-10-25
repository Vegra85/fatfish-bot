# fatfish-bot · 大肥鱼 QQ 机器人（思路 + 工具 + 集成）

「大肥鱼」是作者自己养在电脑上的一个 QQ 群友：DeepSeek 当脑子，[NapCat](https://github.com/NapNeko/NapCatQQ) 接 QQ（OneBot 11 正向 WebSocket）。

**这不是一个下载下来就能用的成品 AI。** 这个仓库给的是思路、工具和集成：怎么接 QQ、怎么在群里接话、掉线了怎么补回、表情包 / 语音 / 唱歌 / 动画怎么接、怎么省 token、怎么做一个本机控制台……
**她是谁、记得什么、心情怎么变、跟谁有多熟 —— 这些「灵魂」不在这里。** 要你自己造一个你自己的 AI。

> 作者每天在跑的版本：Windows 11 + Python 3.15。API key、聊天记录、记忆都不在仓库里。

## 有什么、没有什么

**灵魂：不公开，仓库里放的是「空壳」**（函数都在，但什么都不记、心情一直平静）—— 下载下来照样能跑，你把它们换成自己写的：

| 模块 | 作者的版本做了什么（思路） | 空壳里是什么样 |
|---|---|---|
| `persona.md` 人设 | 她是谁、怎么说话、底线 | 一份模板，照着写你自己的 |
| `memory.py` 记忆 | 攒够一批聊天让模型提炼「长期事实 / 近况 / 经历」，聊天时按新鲜度 + 重要度 + 情绪挑几条想起来；忘掉的留墓碑不再记回来 | 不提炼、不想起（控制台能手动记） |
| `soul.py` 关系 / 日记 / 前情提要 | 每人一张关系卡、她心里挂着的事、滑出上下文的旧聊天压成前情提要、每天写一篇日记、她对自己的认识 | 都不做 |
| `mood2.py` 心情 | 每轮回复后让便宜的模型当裁判打分（开心、委屈、吃醋…），有基准线、会回落、越高越难涨 | 一直「平静」 |
| `galgame.py` 好感度 | 按 QQ 号记好感、分几档、跨档触发小事件 | 不变（控制台能手动改） |
| `cross.py` 群里群外 | 群里私聊是同一个她：外号一样；私聊知道他在群里说过什么；群里不说私聊的事（说漏了会重写） | 各聊各的 |
| `slang.py` 她自己的梗库 | 不认识的梗上网学、记下意思和用法、认得「改了一个字」的老梗 | 不学（梗百科照样能用） |
| `style.py` 跟着群里的节奏说话 | 数群里一句话多长、怎么笑、在刷什么，让她跟着说 | 不加 |
| `shared.py` 共享见闻 | 从各个群提炼「最近大家在聊的事」，所有对话都知道 | 没有 |

每个空壳文件开头都写了 `bot.py` 要用到哪些函数、参数和返回值长什么样。

**工具和集成：都在这里**

| 方面 | 做了什么 | 在哪 |
|---|---|---|
| 聊天 | DeepSeek（`deepseek-flash`，能直接看图）；人设 `bot/persona.md` 改了下一句就生效；聊天记录里每句标上（对你说）（回复 谁）（别人家的 AI），群里多带一点上下文 —— 分得清谁在跟谁说话 | `bot.py` |
| 群聊 | 被 @ / 叫名字 / 引用才回；没 @ 但其实在接她话的也能认出来接上；好几个人同时找她就排队一个一个回；群里有好几个同名机器人时只认她的群名片名 | `followup.py` |
| 认出别人家的 AI | 看着像机器人的号（报错、框架状态输出、成段工整的句子、秒回连发…）攒够几句让模型看一眼，有把握才标成「别的机器人」（只有 @ 她才回、10 分钟最多 3 次）；群友复读机器人报错、真人号挂着机器人框架都不算；控制台里取消了就记成真人 | `aidetect.py` |
| 群聊节奏 | 说上话后在线一阵、冷场了试探一句、到点自然收尾；冷场冒泡（默认只试运行）；「火力全开」：暂时不省 token、接话更宽、更爱插嘴，到点自己关 | `rhythm.py` `speakup.py` `control.py` |
| 上线补回 | 重启 / 掉线 / 关机回来后往回翻聊天记录：私聊都回，群里找过她的人各回一条，晚回先说「刚看到」；私聊里只要有她没回的，开机时和之后每 3 分钟都会补回 | `catchup.py` |
| 删数据 | 私聊发「删除我的数据」再回「确认删除」就真删（群里别人提到他的地方换成「某人」）；控制台里也能删 | `wipe.py` |
| 表情包 | 按情绪、场景、图上的字挑；说了「这张给你」就一定发（限频时提示里会告诉她这轮发不了）；图片要自己准备（见下） | `stickers.py` |
| 语音 | MiniMax 合成她的声音；本地 SenseVoice 听别人的语音 | `voice.py` |
| 唱歌 | 歌单（控制台「唱歌」页：粘 B 站链接导入音轨、上传、设唱哪段）剪一段当 QQ 语音发；点了歌单里没有的歌 → 先找网易云、不行再找 B 站，要歌名（和歌手）完全对上的原唱，截 12 秒左右的高潮；**VIP / 地区限制的歌不绕过**；找不到就直说，不拿别的歌顶替 | `sing.py` `music.py` |
| 梗百科 | 接 [astrbot_plugin_memewiki](https://github.com/in1t1a1/astrbot_plugin_memewiki) 的离线词库（[cnmeme.wiki](https://cnmeme.wiki)）+ 在线接口：有人问「X 是什么梗」先查词库给她看；聊天里出现词库里的梗就提示她一两条；每天凌晨同步新词条 | `memewiki.py` |
| 动画 | 有人要（或她自己想做）时用火山方舟 Seedance 做 5 秒小动画；她自己的形象用头像当参考图；有每天上限和总预算 | `anim.py` |
| 看图 | deepseek-flash 原生看图；本地 Ollama `qwen3-vl` / 智谱 `glm-4v-flash` 兜底；动图拼帧；视频截帧（要 ffmpeg） | `vision.py` `imgprep.py` `media.py` |
| 上网 | DeepSeek 官方搜索 → 百度 → 必应；每天刷几轮 B 站当聊天素材 | `websearch.py` `bili2.py` |
| 省 token | 花得越多回得越少（下面有公式）；别人家 AI 的消息不算进记忆提炼 | `budget.py` |
| 不编 | 不知道就说不知道、做不到就说做不到（系统提示里的硬规矩） | `bot.py` |
| 控制台 | 本机网页 <http://127.0.0.1:8765>：运行状态、QQ 掉线时直接显示扫码二维码、火力全开、功能开关、群和人（静音 / 只在被 @ 时说话 / 不理 / 别的机器人）、代发消息、记忆和好感、表情包、唱歌、统计、用量和余额、日志、人设编辑；代码更新了会自己换成新版本 | `panel.py` `panel.html` |
| 稳 | 守护循环（崩了 15 秒自动拉起）、单实例锁、重启前先在旁边试加载新代码 加载不了就不重启；开机流程记录 | `botloop.bat` `control.py` |

## 怎么跑起来（Windows）

1. 装 Python 3.12 以上，然后 `pip install -r requirements.txt`
2. 装 NapCat（Shell 版）放到 `C:\QQBot\NapCat`，用机器人的 QQ 号扫码登录，打开 OneBot 11 **正向 WebSocket** `127.0.0.1:3001`
3. 把这个仓库放在 `C:\QQBot`（bat 里写的是这个路径）
4. 复制 `bot\config.example.json` 为 `bot\config.json`，至少填：
   - `owner_qq`：你自己的 QQ 号（她的老板）；`bot_name` / `aliases`：她叫什么
   - `llm.deepseek.api_key`：DeepSeek 的 key
   - 要语音再填 `voice.minimax.api_key`（国内版是 platform.minimaxi.com 的 key）
   - 要动画再填 `anim.api_key`（火山方舟的 key，还要在火山方舟控制台开通 Seedance 2.0 mini）
5. **把 `bot\persona.md` 改成你自己的人设**（现在是一份模板）；想让她有记忆、心情、好感……就照着上面那张表把空壳换成你自己写的
6. 打开 `run.bat`，把 `set BOT_QQ=123456789` 改成机器人的 QQ 号
7. 右键 `修复启动.bat` → 以管理员身份运行（建一个计划任务 `QQBot_Service`：登录 Windows 1 分钟后自己启动 QQ 和机器人）；以后用 `启动.bat` / `停止.bat`
8. 双击 `控制台.bat` 打开网页控制台

可选：`安装语音.bat`（本地听写）、`安装本地AI.bat`（Ollama 识图）、`安装视频支持.bat`（ffmpeg，唱歌和视频截帧要用）、`导入表情包.bat`；
梗百科：从 [astrbot_plugin_memewiki](https://github.com/in1t1a1/astrbot_plugin_memewiki) 下载 `data/memes.db`，放到 `bot\data\memewiki\memes.db`（没有它也能跑：只用在线接口）。

**少用整体重启**：`停止.bat` / `启动.bat` 每次都会让 QQ 重新登录，太频繁会被 QQ 判定登录失效、要重新扫码。改设置、改代码只重启机器人就行：控制台里的「重启机器人」、`重启机器人.bat`，或者往 `bot\data\restart.request` 写一行字。

## 省 token：花得越多 回得越少

$$w(u) = f + \frac{1-f}{1+\left(u/u_{1/2}\right)^{k}}$$

- $u$：最近 24 小时花了多少钱（美元；DeepSeek token + MiniMax 语音 + 搜索，按 `config → panel.prices` 折算）
- $u_{1/2}$：花到这么多时意愿掉到一半左右（默认 0.3）；$k$：形状（默认 0.6，小于 1 时一开始掉得快、之后越来越慢）；$f$：最低意愿（默认 0.1）
- 默认参数下：花 \$0.1 → 69%，\$0.3 → 55%，\$1 → 39%，\$5 → 24%
- 私聊不受影响：每条都回。群里不同场合用 $w$ 的不同次方：被 @ / 叫名字 / 引用 $w^{0.1}$（被找的时候几乎都回）、没 @ 的接话 $w^{2.5}$、自己插话 $w^{3}$、冒泡 / 主动找人 $w^{3}$ —— 省 token 主要省在猜「是不是在跟她说」和自己插话上
- 老板的消息永远回；上线补回不受限制；同一个人被跳过后又来找她，回的概率会变高
- 参数在 `config → budget`，控制台「功能开关 → 省 token」能调，「统计」页有曲线

## 表情包

仓库里不带图片。`bot/stickers/` 放图片和 `index.json`（格式见 `stickers.py` 开头的说明）。
nekowhale 动图的标注来自 dsh-nekowhale-stickers（CC BY-NC 4.0）：把它的 `meta/nekowhale.json` 放到 `bot/stickers/nekowhale.json`，再双击 `导入表情包.bat`，会从 kiriyuki.com 下载动图。

## 隐私

- `bot/config.json`（key）、`bot/data/`（聊天记录、记忆、好感度）、日志、表情包图片都在 `.gitignore` 里，不会被提交
- `tools/export_github.py`：作者从自己的运行目录导出到这个仓库用的脚本。只拷白名单里的文件；人设和灵魂类模块换成空壳；清掉 key，换掉 QQ 号和名字；导出前全文检查，发现可疑的就整个不导出
- 控制台只听 `127.0.0.1`，和机器人之间用随机 token
- 谁想让她忘掉自己：私聊她发「删除我的数据」（或者在控制台「记忆和好感」里点删数据）。真删、不留备份；QQ 自己的聊天记录不归这个机器人管

## 更新日志

见 [CHANGELOG.md](CHANGELOG.md)。

## 致谢

- [NapCatQQ](https://github.com/NapNeko/NapCatQQ)、DeepSeek、MiniMax、sherpa-onnx + SenseVoice、Ollama
- 梗百科词库：[astrbot_plugin_memewiki](https://github.com/in1t1a1/astrbot_plugin_memewiki)（in1t1a1，MIT），词条来自 [cnmeme.wiki](https://cnmeme.wiki)
- 思路参考：DSH 社区的 dsh-* 系列（记忆、心情、刷 B 站、冷场冒泡、nekowhale 表情包标注）、qq-bridge（MIT）
- 大肥鱼形象：ZipZipPipe、上善无形；nekowhale 动图：可以叫我云大人 / 雾雪（kiriyuki.com）

## 开源协议

[MIT](LICENSE)：代码和文字随便用、随便改，保留版权声明就行。

大肥鱼的形象图、表情包图片、nekowhale 的标注（CC BY-NC 4.0）、梗百科词库（memes.db，按它自己的 MIT 协议）都不在这个仓库里，也不归这个协议管，要用请按它们各自作者的要求来。
