#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
做东西（10-28 v1.7.0）：「写个网页 / 写段代码 / 画个 SVG」这种大活

  小明 10-28 拿「鹈鹕测试」（让 AI 写一个鹈鹕骑自行车的网页 看它聪不聪明）试她：
  她先反问「要现在就写出来吗」；回复上限 1200 token 把网页截成了半截；整段代码贴在聊天里也不好看
  → 现在：她先用自己的口气嘟囔一句接下来（不反问）→ 另外一轮开深度思考、给足 token 把东西做完整
    → 代码存成文件 用 QQ 文件发过去（私聊文件 / 群文件）；聊天里只留她一句交货的话
  别的回复里冒出来的长代码块 也一样存成文件发（聊天里不贴整段代码）

  want(texts)              他是不是在让她做个东西 → {"kind": "网页", "ext": ".html"} / None
  want_again(texts, last)  刚做完一个（mem["last_make"]）他接着说「说错了 其实我要的是鹈鹕」「重新画一次」「改成…」
                           「求求你现在帮我画」→ 照上一个的种类重做 带上他之前怎么说的（10-28）
  followup(texts, last, hist)    刚交货 他说「返工」又说了哪不对 / 问「改好的呢」「发哪了」→ 重做 / 把上一个再发一次（10-28 v1.9.1）
  after_reply(said, texts, last, hist)  她嘴上答应「这几处我改」/ 说「改完了」「再发一次」但系统没做 → 真去做（10-28 v1.9.1）
  truth_block(last, hist)  刚做过东西：系统记录里最后一次真发的文件、之后他说的改没改（她别自己打「[发了文件…]」就以为发过了）
  find_file(data, last)    上一个文件在电脑上哪（重做时给她看旧的、再发一次用）
  task_like(what)          承诺表里这件事是不是「做个东西」（到点了要真去做 不是嘴上说一句）
  what_of(text)            她交货那段里「做的是：…」那行 → (做的是什么, 剩下的话)（记进聊天记录 以后说得上自己做了啥）
  ack_block(job)           先回的那一句：答应、可以嘟囔麻烦 但别反问、别贴代码、别说「写好了」
  make_block(job, said, old)  真做的那一轮要她怎么写（old = 重做时上一个的完整代码）
  review_block(job, said, code)  做完、发之前再自己查一遍（画的东西：部件挨没挨上、动画会不会飞走）（10-28 v1.9.1）
  wrap_lines(text)         交货那句话里「这就来」「马上画」这种（早答应过了）去掉
  split(text)              回复 → ([(lang, code, closed)], 剩下的话)
  pull_big(text)           回复里够长的代码块拿出来存文件 短的留在原处
  big(code)                这段代码够不够长、该不该存成文件
  ext_of(lang, code, dflt) 扩展名
  file_name(job, code, ext)   文件名（网页 / SVG 的 <title>、第一行注释、种类 + 时间）
  save(folder, name, code)    存文件（同名加 -2、-3…）→ Path
  chunks(code)             文件发不出去时 退回成几条文字
  Quota                    每人每天、总共每天最多做几个（老板不限）
  target_ext(lang, code, job)  这次交什么格式：文档按他要的 / 她挑的；代码照旧看语言（11-02 v1.17.4）

  11-02 v1.17.4（小明：「他目前好像就会生成html文件 我不希望这个被写死」）：
    以前只认网页和代码 ——「帮我写一个docx总结」被当成网页（引用里她那句「写网页做文件」被认成了他要的东西），
    交了个 .html 还说「docx我生不出来」。现在：
    · Word / PDF / Excel / CSV / PPT / Markdown / 文本 都认得；她写 Markdown / 表格 → docmaker 转成真文件
    · 他只说「写个文档」没说格式 → 她按内容自己挑（代码块开头写 ```docx / ```xlsx / ```md / ```html …）
    · 引用的那段（「回复了 X 的「…」」）是别人的话：先只看他自己说的；他自己只说了「写吧」才拿引用里的东西
"""
import json
import re
import time
from pathlib import Path

import clock
import docmaker

BIG_LINES = 12             # 代码块 ≥ 这么多行 或 ≥ BIG_CHARS 个字 → 存成文件发（短的照旧贴在聊天里）
BIG_CHARS = 500

# 他在让她做东西：「写 / 做 / 画 …」后面十几个字以内跟着「网页 / html / 代码 / 脚本 / SVG / 小游戏…」
# （10-28 审查 reply-1：以前「来 / 给我 / 帮我 / 请」也算 后面十几个字以内有「游戏 / 网页 / python」就当成要她做 ——
#   「来玩个成语接龙游戏吧」「给我推荐几个好玩的游戏」「请问这个网页打不开怎么办」「给我个python学习路线」她都答应「这就去弄」）
#   现在：泛泛的请求词（来 / 给我 / 帮我 / 请 / 麻烦）要紧接「一个 / 个 / 份 / 段」、再隔几个字就是东西才算（「请给我一个html」「来个网页」）；
#   中间夹着「推荐 / 找 / 玩 / 想 / 讲 / 问…」、东西后面紧跟「学习 / 路线 / 名字 / 链接 / 题 / 打不开 / 怎么办…」的不算；
#   光说「游戏」（不是小游戏 / 网页游戏 / html）要配 写 / 做 / 弄 / 开发 / 编（「来个猜谜游戏」是要陪他玩）
VERB = r"(?:写|做|弄|整|搞|生成|画|造|实现|开发|编|设计|导出|导成|存成|转成)"   # 导出 / 存成 / 转成（「导出成csv」11-02 v1.17.4）
ASK_VERB = r"(?:来|给我|帮我|帮忙|请|麻烦)(?:一|1)?(?:个|份|段|款|套)"
GAP_NOT = re.compile(r"推荐|找|打不开|怎么办|哪个|还是")
GAP_NOT_ASK = re.compile(r"玩|想|说说|讲|问|学(?![生校])")      # 泛请求词那种 中间再夹这些也不算（「给我个能玩的」）
# 东西后面紧跟着这些：不是让她做。「名字 / 链接 / 题 / 哪个 / 还是」得紧挨着（「游戏名字」「网页链接」）——
#   隔了空格多半是下一句（几句话是用空格连起来的）：「帮我写个小游戏 / 名字叫飞机大战」是让她做（10-28 审查复查 reply-1）
AFTER_NOT = re.compile(r"^(?:[^。！!？?\n，,；;\s]{0,2}?(?:名字|链接|网址|题|哪个|还是)"
                       r"|\s*[^。！!？?\n，,；;\s]{0,2}?(?:学习|路线|教程|编辑器|打不开|怎么办))")
GAME_VERB = re.compile(r"写|做|弄|开发|编")
KINDS = [  # (正则, 种类, 默认扩展名) 先匹配的优先
    # 文件格式（11-02 v1.17.4）：明说了格式的最先认；「文档」没说格式 → auto（她按内容挑）
    (r"docx|\bdoc\b|word|wps", "Word 文档", ".docx"),
    (r"pdf(?!\s*(?:解析|阅读|转换|编辑|插件|库|工具))", "PDF", ".pdf"),
    (r"xlsx|\bxls\b|excel|电子表格|表格文件", "Excel 表格", ".xlsx"),
    (r"csv", "CSV 表格", ".csv"),
    (r"pptx|ppt|幻灯片|演示文稿|slides", "PPT", ".pptx"),
    (r"markdown|\bmd\b", "Markdown 文档", ".md"),
    (r"txt|纯文本|文本文件", "文本文件", ".txt"),
    (r"文档", "文档", "auto"),
    (r"html|网页|页面|前端|h5|web ?page|landing ?page", "网页", ".html"),
    (r"svg", "SVG 图", ".svg"),
    (r"小游戏|游戏", "小游戏", ".html"),
    (r"python|py ?脚本|爬虫", "Python 脚本", ".py"),
    (r"javascript|\bjs\b", "JS 代码", ".js"),
    (r"typescript|\bts\b", "TS 代码", ".ts"),
    (r"c\+\+|cpp", "C++ 代码", ".cpp"),
    (r"\bjava\b", "Java 代码", ".java"),
    (r"\bc ?语言", "C 代码", ".c"),
    (r"rust", "Rust 代码", ".rs"),
    (r"golang|go ?语言", "Go 代码", ".go"),
    (r"批处理|\bbat\b", "批处理", ".bat"),
    (r"powershell|ps1", "PowerShell 脚本", ".ps1"),
    (r"\bsql\b", "SQL", ".sql"),
    (r"latex|\btex\b", "LaTeX", ".tex"),
    (r"脚本", "脚本", ".py"),
    (r"代码|程序|源码|源代码", "代码", ""),
]
_KIND_RE = [(re.compile("(" + VERB + r")([^。！!？?\n]{0,14}?)(" + pat + ")", re.I),
             re.compile("(" + ASK_VERB + r")([^。！!？?\n]{0,6}?)(" + pat + ")", re.I), kind, ext) for pat, kind, ext in KINDS]
# 「请用 SVG 画一只鹈鹕骑自行车」「帮我用 html 做一个时钟」：东西在动词前面（10-28 审查复查 reply-1：泛请求词收窄以后这种认不出来了）
_USE_RE = [(re.compile(r"(?:帮我|给我|替我|请你?|麻烦你?|帮忙)\s*(?:用|拿)\s*(?:" + pat + r")\s*(?:来|帮我|给我)?\s*" + VERB, re.I),
            kind, ext) for pat, kind, ext in KINDS if kind != "小游戏"]
# 不是让她做：讲 / 解释 / 看看 / 改改 / 报错 这些是在问东西（她照常回答 有长代码一样会存成文件）
NOT_MAKE = re.compile(r"讲讲|讲解|讲一下|解释|分析一下|看看|看下|看一下|帮我看|改改|改一下|优化一下|调试|debug|报错|出错|"
                      r"什么意思|啥意思|怎么用|怎么装|怎么运行", re.I)
# 「你会写代码吗」= 问她会不会 不是让她写
ABILITY = re.compile(r"^(?:你)?(?:会不会|能不能|会|能)(?:写|做|画|编)[^帮给请]{0,10}(?:吗|么|嘛|不)[?？。!！~～]*$")

# 刚做完一个东西 他接着要改 / 重做（10-28：「说错了 其实我要的是鹈鹕」「你能重新画一次吗」「不行我真的很需要求求你现在帮我画」
# 三句都没认出来 —— 没有「网页 / svg」这种词 —— 她只好嘴上答应「明早给你画」 结果什么都没做）
AGAIN = re.compile(
    r"重新(?:画|做|写|弄|整|来|生成)|重画|重做|重写|重来一|再(?:画|做|写|弄|整|来)(?:一|个|张|份|次|遍|下)|"
    r"改成|换成|画成|做成|写成|弄成|改一下|改改|改下|改个|加(?:个|一个|上|点)|去掉|删掉|"
    r"画错|做错|写错|弄错|(?<![你她他])(?:说|打)错了|"
    r"其实(?:我)?(?:要|想要)|我(?:要|想要)的是|不是要|"
    r"(?:帮我|给我|快|赶紧|现在|马上|立刻|这就)(?:画|做|写|弄|改)|"
    r"(?:能|可以|再)(?:不能)?(?:帮我)?(?:重新)?(?:画|做|写|弄|改)(?:一|个|下|张|次|吗|么|嘛)")
AGAIN_NOT = re.compile(r"讲讲|讲解|讲一下|解释|什么意思|啥意思|怎么用|怎么打开|怎么运行|打不开")
# （11-10 v1.17.35 审查 V5-14）上面的 AGAIN 一命中就开做、后面没有第二道：交货后 6 小时内「我现在做饭去了」「咖啡加点糖」「加个好友吧」
#   → 她真的再做一份文件发过去；正在做时他问「快做好了吗」（快 + 做）→ 那件被取消、从头重做。现在 want_again 分开认：
#   AGAIN_SURE 明说重来的（重做 / 重画 / 重新画 / 再画一个…）、说了错又说了要什么的（说错了 其实我要的是…）：照旧，6 小时内都算
#   AGAIN_SOFT 只是带「改成 / 加个 / 去掉 / 说错了 / 其实我要 / 帮我画」这种字的：得看得出是冲那件东西说的才算 ——
#              那件正在做、刚交货他紧接着说的（AGAIN_HOT_SEC 以内、交货以后没隔几条）、或者话里提到了那件东西（about）；
#              「帮我 / 快 / 现在 + 画做写弄改」后面得是句子的边（「现在帮我画」算；「现在做饭去了」「马上写作业」「帮我写个请假条」不算），
#              「我现在弄」「我马上写」是他在说自己 不算
#   AGAIN_ASK  问进度的（好了吗 / 做完没 / 快做好了吗 / 做到哪了 / 还要多久）：不算重做、不打断正在做的
#   拿不准一律不算：漏了的还有两道接着 —— 她嘴上答应了「我改」→ after_reply 真去做；她在状态行写「做东西」→ 照做
_AGAIN_ERR = r"画错|做错|写错|弄错|(?<![你她他])(?:说|打)错了"
_AGAIN_WANT = r"其实(?:我)?(?:要|想要)|我(?:要|想要)的是|不是要"
AGAIN_SURE = re.compile(r"(?:(?<!我)|(?<=帮我|给我|替我))(?:重新(?:画|做|写|弄|整|来|生成)|重画|重做|重写)(?!了)|重来一|"   # 「我重新做了一遍作业」「这题我重做了」是他在说自己
                        r"再(?:画|做|写|弄|整)(?:一(?!会)|个|张|份|次|遍|下)|再来(?:一?[个张份次遍版幅]|一?下)|"   # 「再写一会儿」「再来一杯」不算
                        r"(?:" + _AGAIN_ERR + r")[^。！？!?\n]{0,12}?(?:" + _AGAIN_WANT + r")")                     # 「说错了 其实我要的是鹈鹕」：说了错、又说了要什么
_AGAIN_END = r"(?:吧|嘛|呗|啊|呀|哦|吗|么|好吗|行吗|好不好|行不行|可以吗)*(?![0-9A-Za-z\u4e00-\u9fff])"     # 后面是句子的边
_AGAIN_N = r"一?[下个张份次遍版幅]"
AGAIN_SOFT = re.compile(
    r"改成|换成|画成|做成|写成|弄成|改一下|改改|改下|改个|加(?:个|一个|上|点)|去掉|删掉|"
    + _AGAIN_ERR + "|" + _AGAIN_WANT + "|"
    r"(?:(?:帮我|给我|替我)(?:再|重新)?|(?<!我)(?:快|赶紧|现在|马上|立刻|这就)(?:点|就)?(?:帮我|给我)?)(?:画|做|写|弄|改)(?:" + _AGAIN_N + r")?" + _AGAIN_END + r"|"
    r"(?:能不能|可不可以|能|可以|再)(?:再)?(?:帮我|给我)?(?:画|做|写|弄|改)(?:" + _AGAIN_N + r"|吗|么|嘛)" + _AGAIN_END)
AGAIN_ASK = re.compile(r"(?:好|完|成)了(?:吗|没|么|嘛)|(?:好|完)了?没(?:有)?(?![0-9A-Za-z\u4e00-\u9fff])|(?:画|做|写|弄|改)到哪|"
                       r"(?:还要|还有|还得)多久|多久(?:能)?好|什么时候(?:能)?(?:好|(?:画|做|写|弄|改)[完好])|还没(?:好|完)")
AGAIN_HOT_SEC = 600        # 交货以后多久以内、
AGAIN_HOT_GAP = 6          # 聊天记录没隔过几条 → 算「刚交货他紧接着说的」
NOT_BUT = re.compile(r"不是([^，,。！？!?\s]{1,8}?)[，,\s]*(?:是|要)")      # 「不是醍醐 是鹈鹕」：前半截得是他上次说过的
AGAIN_SEC = 6 * 3600       # 上一个做完多久以内 还算在说它

# 交货以后（10-28 05:03 v1.9.1）：他说「你画得有问题返工」→ 她问哪不对 →「腿的动作没跟脚踏连接鱼在空中漂浮 还有不知名圆圈在飞」
# → 她说「这几处我改」—— 没有「重画 / 改成」这种词 系统没认出来 什么都没做；他问「改了的文件呢」→ 她说「改完了」
# 还自己打了一行「[发了文件…]」（那是系统记的格式）→ 那行进了聊天记录 后面她一口咬定「我这显示发了呀」
FIX_SEC = 3600             # 交货以后多久以内 他说「哪不对」「改好的呢」还算在说它
FIX_GAP = 12               # 交货以后最多隔几条聊天记录
REDO = re.compile(r"返工|重做|重新做|重新弄|再改|修改|改一?下|改改|修一?下|调一?下|有问题|不对(?!称)|有毛病|有bug", re.I)
FIX_SPEC = re.compile(     # 具体说了哪不对
    r"没(?:有)?(?:跟|和|与|连|接|对上|对齐|贴|踩|挨|碰|在|动|转|显示|画)|"
    r"漂浮|悬空|飘(?:着|在|起来|走)|(?:在|乱|到处)飞|飞(?:走|出去|出来|来飞去)|"
    r"歪|斜了|穿模|错位|重叠|叠在一起|挡住|盖住|断开|断了|脱节|分家|散架|"
    r"不(?:动|转|连|跟|贴|像|对称|协调|自然)|多(?:了|出)|少了|缺了|"
    r"太(?:大|小|丑|暗|亮|快|慢|假|怪|挤|空)|看不(?:清|出)|白屏|空白|乱码")
_VAGUE = re.compile(r"返工|重做|重画|有问题|问题|不对|错了|bug|毛病|改一?下|改改|修一?下|修改|调一?下|"
                    r"[你她他我画做写弄得的地有再改这那个还是就了啊吧呢吗么嘛呀哦噢额呃哈]|[\s，。！？、,.!?~～…]", re.I)
# 他在问文件：「改了的文件呢」「你他妈发哪了」「没收到」「改好了吗」
WHERE = re.compile(
    r"(?:文件|网页|图|画|新的|改的|改好的|改了的|重画的|重做的|东西)[^。！？?!\n]{0,8}?(?:呢|在哪|发哪|去哪|没(?:收到|看到|看见|发))|"
    r"发哪|发到哪|没(?:收到|看到|看见|发过来|发来)|收不到|看不到文件|(?:改|画|做|弄)(?:好|完)了(?:吗|没|么|嘛)")
# 她嘴上答应现在就改 / 说改完了、再发一次（系统这轮却什么都没做）
PROMISE = re.compile(
    r"(?:我|这就|马上|现在|立刻|立马|这几处|这些|这块|这里)(?:就|去|再|重新|这就|给你|帮你|来|都)*"
    r"(?:改|重画|重做|重写|返工|修)(?!了|的|得|过|好了|完了|天|日|名|口)|"
    r"重新(?:画|做|弄|写)(?:一|个|张|份|下|遍)|再(?:画|做|弄|写)(?:一|个|张|份|遍)|改(?:好|完)(?:了)?(?:就|再)(?:发|给)")
CLAIM_FIX = re.compile(r"改(?:好|完)了(?![就再]?(?:发|给))|(?:已经|刚)改|我(?:都|已经|刚)?改了(?!主意|名)")   # 说改过了
CLAIM = re.compile(CLAIM_FIX.pattern + r"|(?:已经|刚)发|发(?:你|给你|过去)了|"                          # 说改过了 / 发过了
                   r"再(?:甩|发|传|丢)(?:你|给你)?(?:一|1)(?:次|遍|下)|(?:重新|又)发(?:了|一)")
RESEND_SAY = re.compile(r"再(?:甩|发|传|丢)(?:你|给你)?(?:一|1)?(?:次|遍|下|份)|重新发|又发")
NO_DO = re.compile(r"不改|不想改|改不了|没法改|不会改|懒得改|不重画|不画了|不做了|不弄了|就这样吧")
USER_STOP = re.compile(r"不急|明天再|改天|以后再|不用改|别改了|算了|先别|不用了")
# 承诺表里的事是「做个东西」：画 / 写 / 做 + 东西，或者「把画改成…」「重画」
TASK_MAKE = re.compile(r"(?:画|写|做|改|弄|生成|重画|重做)[^。！!？?\n]{0,14}?(?:网页|html|svg|代码|脚本|小游戏|游戏|程序|画|图|文档|docx|word|pdf|表格|excel|xlsx|csv|ppt|幻灯片|markdown|txt)|"
                       r"(?:把|将)(?:画|图|网页|代码|它|那张)[^。！!？?\n]{0,6}?(?:改|换|重)|重画|重新画|重做|重新做", re.I)
WHAT_LINE = re.compile(r"^[ \t]*[（(]?\s*做的是[:：]\s*(.+?)\s*[)）]?[ \t]*$", re.M)

LANG_EXT = {"html": ".html", "htm": ".html", "svg": ".svg", "xml": ".xml", "python": ".py", "py": ".py",
            "python3": ".py", "javascript": ".js", "js": ".js", "typescript": ".ts", "ts": ".ts", "css": ".css",
            "json": ".json", "c": ".c", "cpp": ".cpp", "c++": ".cpp", "java": ".java", "go": ".go", "golang": ".go",
            "rust": ".rs", "rs": ".rs", "bash": ".sh", "sh": ".sh", "shell": ".sh", "zsh": ".sh", "bat": ".bat",
            "batch": ".bat", "cmd": ".bat", "powershell": ".ps1", "ps1": ".ps1", "sql": ".sql", "markdown": ".md",
            "md": ".md", "latex": ".tex", "tex": ".tex", "yaml": ".yml", "yml": ".yml", "kotlin": ".kt",
            "swift": ".swift", "php": ".php", "ruby": ".rb", "lua": ".lua", "csharp": ".cs", "c#": ".cs", "cs": ".cs",
            "vue": ".vue", "jsx": ".jsx", "tsx": ".tsx", "r": ".r", "matlab": ".m", "txt": ".txt", "text": ".txt"}

FENCE = re.compile(r"```[ \t]*([\w+#.-]*)[^\n]*\n(.*?)(\n[ \t]*```|\Z)", re.S)
BAD_NAME = re.compile(r'[<>:"/\\|?*\x00-\x1f]+')


QUOTE = re.compile(r"（回复了[^「」（）\n]{0,40}?的「.*?」）", re.S)


def want(texts):
    """这一轮他在让她做个东西？texts = 他还没被回的那几句 → {"kind", "ext"} / None
    11-02 v1.17.4：先只看他自己说的 —— 引用的那段是别人的话（小明 引用她那句「写网页做文件」说「帮我写一个docx」→ 以前认成网页）；
    他自己只说了「写吧」「做一个」认不出东西 才连引用一起看"""
    full = " ".join(str(x or "") for x in (texts or []))[-600:]
    own = QUOTE.sub(" ", full)
    hit = _want(own)
    if hit is None and own != full and re.search(VERB, own):
        hit = _want(full)
    return hit


def _want(t):
    if not t.strip():
        return None
    # 带「讲讲 / 看看 / 解释…」= 在问东西 不是让她做（10-28）；11-02 v1.17.4：要文档的那几种 这些词在后面的照样算
    #   （「给我写个文档 讲讲今天群里聊了啥」= 文档里要讲的）—— 只有它们出现在「写个文档」前面才不算
    nm = NOT_MAKE.search(t)
    for line in str(t).splitlines() or [t]:
        if ABILITY.search(line.strip()) and not re.search(r"帮我|给我|请|麻烦", line):
            return None
    for rx, rx_ask, kind, ext in _KIND_RE:
        doc = ext == "auto" or ext in docmaker.EXTS
        if nm and not doc:
            continue
        for r in (rx, rx_ask):
            pos = 0
            while True:                    # 每个起点都试一遍（前面那段不算 后面的「写个网页」还能认出来）
                m = r.search(t, pos)
                if not m:
                    break
                pos = m.start() + 1
                if nm and nm.start() < m.start():
                    continue
                before = t[max(0, m.start() - 4):m.start()]
                # 「让我写 / 教我写」不算叫她写（「老师让我写个网页 好难」「教我写个网页」10-28 审查复查 reply-1）
                if before.endswith("在") or re.search(r"(?<![帮给替为])(?:我|我们|俺)(?:也|又|还|刚|正)?$", before):
                    continue               # 「我在写代码好累」「你在写代码吗」是在说自己 / 问她 不是让她写（「帮我写」「给我做」照算）
                if GAP_NOT.search(m.group(2)) or (r is rx_ask and GAP_NOT_ASK.search(m.group(2))) \
                        or AFTER_NOT.search(t[m.end():]):
                    continue               # 「给我推荐几个游戏」「给我个python学习路线」「你做的网页打不开」（10-28 审查 reply-1）
                if m.group(3).lower() == "游戏" and (r is rx_ask or not GAME_VERB.fullmatch(m.group(1))):
                    continue               # 「来个猜谜游戏」「整个游戏都很好玩」（10-28 审查 reply-1）
                return {"kind": kind, "ext": ext}
    for rx, kind, ext in _USE_RE:          # 「请用 SVG 画…」「帮我用 html 做…」（10-28 审查复查 reply-1）
        if nm and not (ext == "auto" or ext in docmaker.EXTS):
            continue
        if rx.search(t):
            return {"kind": kind, "ext": ext}
    return None


def _again_hit(t, last, now=None, hist=None, busy=False):
    """他这句算不算在要改 / 重做上一个（11-10 v1.17.35 审查 V5-14，规矩见 AGAIN_SURE 上面那段）"""
    if AGAIN_ASK.search(t):
        return False                       # 问进度：不算重做（正在做的接着做；刚交货的交给 followup 看要不要再发一次）
    if AGAIN_SURE.search(t):
        return True
    if not AGAIN_SOFT.search(t):
        return False
    if busy or about(last, [t]):
        return True
    ts = float(last.get("ts") or 0)        # 刚交货他紧接着说的：hist = 聊天记录（已经包括他这几句）
    since = [h for h in (hist or []) if isinstance(h, dict) and float(h.get("ts") or 0) > ts]
    return hist is not None and (now or time.time()) - ts <= AGAIN_HOT_SEC and len(since) <= AGAIN_HOT_GAP


def want_again(texts, last, now=None, hist=None, busy=False, sure=False):
    """上一个做的东西还热乎（mem["last_make"] 6 小时内）、他这句在要改 / 重做 → 照上一个的种类再做一个；不是 → None
    （11-10 v1.17.35 审查 V5-14）hist = 聊天记录、busy = 那件正在做：回他那一轮传进来 认得严一点（_again_hit）；
    sure=True = 已经定了要做（她自己在状态行写了「做东西」/ 承诺表里到点的那件）：照旧宽着认（AGAIN）"""
    if not isinstance(last, dict) or not last.get("kind"):
        return None
    if (now or time.time()) - float(last.get("ts") or 0) > AGAIN_SEC:
        return None
    t = " ".join(str(x or "") for x in (texts or []))[-400:]
    if not t.strip() or AGAIN_NOT.search(t):
        return None
    if not (AGAIN.search(t) if sure else _again_hit(t, last, now, hist, busy)):
        m = NOT_BUT.search(t)
        old = " ".join([str(x) for x in (last.get("said") or [])] + [str(last.get("file") or ""), str(last.get("what") or "")])
        if not (m and m.group(1) in old):
            return None
    return {"kind": last["kind"], "ext": last.get("ext") or "", "again": True,
            "before": [str(x)[:300] for x in (last.get("said") or [])][-4:],
            "file": str(last.get("file") or ""), "what": str(last.get("what") or ""), "path": str(last.get("path") or "")}


def task_like(what):
    return bool(TASK_MAKE.search(str(what or "")))


_GENERIC = set("画图做写改弄给他她你的了个一张把成换重新网页代码")


def _grams(x):
    """两个字一组（英文 / 数字按整词、三个字母以上）；两个字都是「画图做写…」这种泛字的不算"""
    x = re.sub(r"[^\u4e00-\u9fffA-Za-z0-9]", " ", str(x or "").lower())
    out = set()
    for w in x.split():
        if re.fullmatch(r"[a-z0-9]+", w):
            if len(w) >= 3:
                out.add(w)
            continue
        for i in range(len(w) - 1):
            g = w[i:i + 2]
            if not (g[0] in _GENERIC and g[1] in _GENERIC):
                out.add(g)
    return out


def same_thing(what, texts):
    """承诺表里这件事跟刚做的是不是同一件（有个两字以上的词对得上：「把画改成鹈鹕」↔「其实我要的是鹈鹕」）"""
    a = _grams(what)
    if not a:
        return True                        # 「把画改了」这种没说是哪个的：就是刚才那个
    b = set().union(*[_grams(t) for t in (texts or [])]) if texts else set()
    return bool(a & b)


def what_of(text):
    """交货那段里「做的是：…」那一行拿出来 → (做的是什么, 剩下的话)"""
    text = str(text or "")
    m = WHAT_LINE.search(text)
    if not m:
        return "", text.strip()
    rest = (text[:m.start()] + text[m.end():]).strip()
    return m.group(1).strip()[:60], re.sub(r"\n{2,}", "\n", rest)


def _since(last, hist, now=None):
    """交货以后的聊天记录（交货 FIX_SEC 以内、中间最多 FIX_GAP 条）→ list；不算在说它了 → None"""
    if not isinstance(last, dict) or not last.get("kind"):
        return None
    t = float(last.get("ts") or 0)
    if (now or time.time()) - t > FIX_SEC:
        return None
    since = [h for h in (hist or []) if isinstance(h, dict) and float(h.get("ts") or 0) > t]
    return since if len(since) <= FIX_GAP else None


def _concrete(text):
    """去掉「返工 / 有问题 / 你画得…」这些 还剩三个字以上 = 说了具体哪不对"""
    return len(_VAGUE.sub("", str(text or ""))) >= 3


def _where_only(text):
    """只是在问文件在哪（没说要改什么）"""
    t = str(text or "")
    return bool(WHERE.search(t)) and not (FIX_SPEC.search(t) or AGAIN.search(t))


def _feedback(users):
    """交货以后他说的话里 在说要改的那几句（问「文件呢」的、问「什么意思 / 怎么打开」的不算）"""
    out = []
    for u in users:
        u = str(u or "").strip()
        if not u or AGAIN_NOT.search(u) or _where_only(u):
            continue
        if REDO.search(u) or FIX_SPEC.search(u) or AGAIN.search(u):
            out.append(u[:300])
    return out


def _fix_job(last, fb, **more):
    return {"kind": last["kind"], "ext": last.get("ext") or "", "again": True, "fix": True,
            "before": [str(x)[:300] for x in (last.get("said") or [])][-4:],
            "file": str(last.get("file") or ""), "what": str(last.get("what") or ""),
            "path": str(last.get("path") or ""), "said": [str(x)[:300] for x in fb][-6:], **more}


def followup(texts, last, hist=None, now=None):
    """刚交货（mem["last_make"] FIX_SEC 以内、中间没隔几条）他接着说的 texts（hist = 聊天记录 已经包括 texts）：
      - 问「改了的文件呢」「发哪了」「没收到」：交货以后说过要改 → ("fix", job)；没说过 → ("resend", None) 真的再发一次
      - 「返工 / 有问题 / 改一下」（这句或者交货以后前面那句）+ 这句说了具体哪不对 → ("fix", job)
      - 只说「有问题」「返工」没说哪：None（她问清楚）；别的也是 None（她答应了「我改」→ after_reply 兜底）"""
    since = _since(last, hist, now)
    if since is None:
        return None
    cur = " ".join(str(x or "") for x in (texts or [])).strip()
    if not cur or AGAIN_NOT.search(cur):
        return None
    users = [str(h.get("text") or "") for h in since if h.get("role") == "user"]
    if WHERE.search(cur) and not (FIX_SPEC.search(cur) or AGAIN.search(cur)):
        fb = _feedback(users)
        return ("fix", _fix_job(last, fb, where=True)) if fb else ("resend", None)
    redo_before = any(REDO.search(u) for u in users if u not in (texts or []))
    if (redo_before or REDO.search(cur)) and FIX_SPEC.search(cur) or REDO.search(cur) and _concrete(cur):
        return ("fix", _fix_job(last, _feedback(users) or [cur]))
    return None


def after_reply(said, texts, last, hist=None, now=None):
    """她这句已经发出去了：嘴上答应「这几处我改」、或者说「改完了 / 再发你一次」—— 系统这轮却什么都没做（10-28 05:05）
       → ("fix", job) 照他交货以后说的重做 / ("resend", None) 把上一个文件真的再发一次 / None"""
    said = str(said or "")
    if not said or NO_DO.search(said) or USER_STOP.search(" ".join(str(x or "") for x in (texts or []))):
        return None
    since = _since(last, hist, now)
    if since is None:                      # 交货一小时以后：还欠着他要的改动、她这句又答应改 / 说改好了（说的是那个东西）→ 真去改（10-28 23:09）
        fb = pending(last, hist, now)
        if fb and (PROMISE.search(said) or CLAIM_FIX.search(said) or RESEND_SAY.search(said)) \
                and about(last, [said] + [str(x or "") for x in (texts or [])] + _her_recent(hist, 3)):
            return "fix", _fix_job(last, fb)
        return None
    users = [str(h.get("text") or "") for h in since if h.get("role") == "user" and str(h.get("text") or "").strip()]
    if PROMISE.search(said) or CLAIM_FIX.search(said):     # 答应了改 / 说改完了（其实没改）：交货以后他说的（问「文件呢」的除外）就是要改的
        fb = [u[:300] for u in users if not _where_only(u)]
        if fb:
            return "fix", _fix_job(last, fb)
    if CLAIM.search(said):                 # 说发了：交货以后他真说过要改 → 去改；没说过、她说「再发一次」→ 真的再发
        fb = _feedback(users)
        if fb:
            return "fix", _fix_job(last, fb)
        if RESEND_SAY.search(said):
            return "resend", None
    return None


# 欠着没改的（10-28 23:09 v1.11.1）：05:03 交货 → 05:05 他说「腿的动作没跟脚踏连接 鱼在空中漂浮 还有不知名圆圈在飞」→ 她答应了改、
# 那时（v1.9.1 以前）系统没去改；她还说「改完了」「再甩你一次」 其实一个文件都没发 —— 这个「改完了」被记成了挂念 →
# 22:39 她主动问「鹈鹕骑车那个改好了 你收到没」→ 他说「没」→ 她说「行 我再弄一次 你等着」系统照样没动
# （followup / after_reply 只管交货一小时以内、truth_block 只管 6 小时以内）
PENDING_SEC = 48 * 3600    # 交货以后他说要改、一直没改：这么久以内还算欠着他的
ASKED = re.compile(r"收到(?:了)?(?:没|吗|么|嘛|没有)|看到(?:了)?(?:没|吗|么|没有)|打(?:得)?开(?:了)?(?:吗|没|么)|能打开(?:吗|没|么)")
NO_ANS = re.compile(r"^\s*(?:没|没有|没呢|没啊|没呀|没收到|没看到|没看见|木有|还没|无|no|nope)\s*[。.!！~～…?？]*\s*$", re.I)
ASK_DONE = re.compile(r"呢\s*[?？]*\s*$|在哪|(?:改|弄|画|做)(?:好|完)了?(?:吗|没|么|嘛|没有)|(?:改|弄|画|做)了(?:吗|没|么|嘛|没有)")
# 他没提是哪个、但她刚才几句在说它：「弄好了没」「好了吗」「还没好？」（「你在干嘛呢」这种不算）
ASK_DONE2 = re.compile(r"(?:改|弄|画|做|发)(?:好|完)?了?(?:吗|没|么|嘛|没有)|^\s*(?:那个)?好了(?:吗|没|么|嘛|没有)|还没好|在哪|发哪|没收到")
CANCEL = re.compile(r"不用改|别改了|算了|不用了|不要了|不改了|就这样吧|这样就行")
_ABOUT_SKIP = {"html", "htm", "svg", "css", "png", "jpg", "txt", "json", "docx", "xlsx", "pdf", "pptx", "csv", "md"}


def pending(last, hist=None, now=None):
    """交货以后他说过要改、之后一直没真发过新文件（PENDING_SEC 以内）→ 他说要改的那几句；没欠着 → []
    「说过要改」只认刚交货那一阵（跟 followup 一样：FIX_SEC 以内、交货以后前 FIX_GAP 条）—— 后来随口说的「太快了」「我没在家」不算；
    做好了新版本 last_make 就换了 → 自然不欠了；他后来说「算了 / 不用改了」→ 不欠了"""
    if not isinstance(last, dict) or not last.get("kind") or not last.get("file"):
        return []
    t = float(last.get("ts") or 0)
    if (now or time.time()) - t > PENDING_SEC:
        return []
    since = [h for h in hist or [] if isinstance(h, dict) and float(h.get("ts") or 0) > t]
    fb, off = [], False
    for i, h in enumerate(since):
        if h.get("role") != "user":
            continue
        u = str(h.get("text") or "").strip()
        if not u:
            continue
        early = i < FIX_GAP and float(h.get("ts") or 0) - t <= FIX_SEC
        if early and _feedback([u]):
            fb.append(u[:300])
            off = False
        elif fb and CANCEL.search(u):
            off = True
    return [] if off else fb[-6:]


def _title(last):
    """文件名里说的是什么：「鹈鹕 · 河边骑车.html」→「鹈鹕」（· 前面那段是画的东西 后面是场景 —— 「骑车」「河边」太常见 不拿来认）"""
    t = re.sub(r"\.[A-Za-z0-9]{1,5}$", "", str((last or {}).get("file") or ""))
    t = re.sub(r"[-_ ]?\d+$", "", t)
    parts = [x.strip() for x in re.split(r"\s*[·・]\s*", t) if x.strip()]
    return parts[0] if len(parts) >= 2 else t


def about(last, texts):
    """这几句是不是在说那个东西：文件名里有两个字对得上（「鹈鹕骑车那个」↔「鹈鹕 · 河边骑车.html」）；
    文件名里没有能对的 → 「做的是什么」里得对上两组"""
    if not isinstance(last, dict):
        return False
    b = set().union(*[_grams(x) for x in (texts or []) if x]) - _ABOUT_SKIP if texts else set()
    if not b:
        return False
    ta = _grams(_title(last)) - _ABOUT_SKIP
    if ta:
        return bool(ta & b)
    return len((_grams(last.get("what")) - _ABOUT_SKIP) & b) >= 2


def _her_recent(hist, n=3):
    """他这几句之前 她最近说的几句（她刚回的这句不算；早的在前）"""
    out, seen = [], False
    for h in reversed(hist or []):
        if not isinstance(h, dict):
            continue
        if h.get("role") == "user":
            seen = True
        elif h.get("role") == "assistant" and seen:
            out.append(str(h.get("text") or ""))
            if len(out) >= n:
                break
    return out[::-1]


def _her_before(hist):
    """他这几句之前 她说的最后一句（她刚回的这句不算）"""
    r = _her_recent(hist, 1)
    return r[-1] if r else ""


def owed(texts, last, hist=None, now=None):
    """交货一小时以后（followup 不管了）他问起那个还欠着没改的东西 → ("fix", job)；不是 → None
    - 她刚问「鹈鹕骑车那个…收到没」他说「没」（10-28 23:09）
    - 他说到了那个东西、又在问「…呢」「改好了吗」「在哪」「没收到」"""
    fb = pending(last, hist, now)
    if not fb:
        return None
    cur = " ".join(str(x or "") for x in (texts or [])).strip()
    if not cur or AGAIN_NOT.search(cur) or CANCEL.search(cur):
        return None
    her = _her_before(hist)
    if NO_ANS.match(cur) and ASKED.search(her) and about(last, [her]):
        return "fix", _fix_job(last, fb, where=True)
    if about(last, texts) and (WHERE.search(cur) or ASK_DONE.search(cur)):
        return "fix", _fix_job(last, fb, where=True)
    if len(cur) <= 20 and ASK_DONE2.search(cur) and about(last, _her_recent(hist, 3)):
        return "fix", _fix_job(last, fb, where=True)     # 她刚说「我再弄一次 你等着」他问「弄好了没」（10-28 23:09 之后最可能的那句）
    return None


def fix_job(last, fb, **more):
    """bot.py 主动找他时用：照他交货以后说的重做（跟 followup / after_reply 给的一样）"""
    return _fix_job(last, fb, **more)


def owed_scene(last, fb, now=None):
    """主动找他、心里挂着的正是这件欠着没改的事（10-28 22:39）：照实说 别说「改好了 你收到没」"""
    ago = _ago((now or time.time()) - float((last or {}).get("ts") or 0))
    return (f"系统记录（以这个为准）：你最后一次真发给他的文件是{ago}的「{(last or {}).get('file')}」；"
            f"之后他说「{' / '.join(x[:60] for x in fb[-3:])}」要改 —— 你答应了 但一直没改、也没发过新文件"
            "（之前说的「改完了」「再发你一次」都没真发出去）。现在主动去找他：老实说那个还没改好、你这就改"
            "（系统马上就真的去改 改好了会把文件发给他）。别说「改好了」「你收到没」，别贴代码。")


def _ago(sec):
    sec = max(0, int(sec))
    if sec < 90:
        return "刚才"
    if sec < 3600:
        return f"{sec // 60} 分钟前"
    return f"{sec // 3600} 个多小时前"


def truth_block(last, hist=None, now=None, busy=None, force=False):
    """刚做过东西（6 小时内、之后没聊太久）：系统记录里最后一次真发的文件是哪个、交货以后他说要改的改没改
    （10-28 05:06：她自己打了一行「[发了文件…]」就以为发过了 →「我这显示发了呀」「你那边刷不出来？」）
    force：正在说那件还欠着没改的东西（PENDING_SEC 以内）—— 过了 6 小时、中间聊了很多也照样给她看（10-28 23:09）"""
    if not isinstance(last, dict) or not last.get("file"):
        return ""
    now = now or time.time()
    t = float(last.get("ts") or 0)
    if now - t > (PENDING_SEC if force else AGAIN_SEC):
        return ""
    since = [h for h in (hist or []) if isinstance(h, dict) and float(h.get("ts") or 0) > t]
    if len(since) > 3 * FIX_GAP and not force:
        return ""                          # 早就聊别的去了
    s = (f"\n# 你做过的东西（系统记录 —— 发没发过文件 以这个为准）\n"
         f"- {_ago(now - t)}你真发出去一个文件「{last['file']}」" + (f"（做的是：{last['what']}）" if last.get("what") else "")
         + "，这是你最后一次真发出去的文件\n")
    fb = _feedback([str(h.get("text") or "") for h in since if h.get("role") == "user"])
    if busy:
        s += "- 他要的新版本 系统正在做 还没做好\n"
    elif fb:
        s += "- 交货以后他说的「" + " / ".join(x[:60] for x in fb[-3:]) + "」—— 你还没照着改过、也没发过新文件\n"
    return s + ("- 只有系统真把文件发出去了才算发了：没发就别说「改完了」「发你了」「再发一次」，更别自己打「[发了文件…]」"
                "（那是系统记录用的 你打出来不是文件）。他说没收到 就照这个说实话\n")


def resend_block(last, now=None):
    """他说没看到文件、交货以后也没说要改什么 → 系统把那个文件再发一次（她说一句就行）"""
    ago = _ago((now or time.time()) - float((last or {}).get("ts") or 0))
    return (f"\n（他说没看到你发的文件 —— 系统记录里{ago}确实发过「{(last or {}).get('file')}」。"
            "你说一声会再发一次就行 系统会把那个文件真的再发一遍；别说你改过了）\n")      # （11-11 v1.17.38 E1：不给现成的话）


def find_file(data, last):
    """上一个文件在电脑上哪：last_make["path"]（相对 data 文件夹）；以前的没记路径 → 在 data/files/ 底下按名字找最新的"""
    if not isinstance(last, dict) or not last.get("file"):
        return None
    data = Path(data)
    rel = str(last.get("path") or "")
    if rel:
        q = data / rel
        if q.is_file():
            return q
    name = Path(str(last["file"])).name
    root = data / "files"
    # 回话自检（replytest 的「画鹈鹕」）做的放在 files/selftest/ —— 不是发给谁的那份（10-28：自检的「鹈鹕 · 河边骑车.html」比真发的新 以前会被当成上一个文件）
    hits = [d / name for d in root.iterdir() if d.is_dir() and d.name != "selftest" and (d / name).is_file()] if root.is_dir() else []
    return max(hits, key=lambda q: q.stat().st_mtime) if hits else None


# 交货那段里「这就来」「马上画」（10-28 05:03：先答应过一句了 交货时又来一句「鹈鹕这就来」）
ACK_LINE = re.compile(r"这就(?:来|去|画|弄|做|写|改|开工)|马上(?:就)?(?:来|画|弄|做|写|改|开工)|稍等|等(?:我|一下|会儿)|"
                      r"开工|去(?:画|弄|做)了|正在(?:画|弄|做|写)")


def wrap_lines(text):
    lines = [x for x in re.split(r"\n+", str(text or "")) if x.strip()]
    return "\n".join(x for x in lines if not ACK_LINE.search(x))


TYPO = ("他打的字可能有输入法打错的同音字：要做的东西照字面说不通（「醍醐在河边骑车」—— 醍醐不是会骑车的东西 多半是「鹈鹕」）"
        "就按最说得通的那个来")


def _prev(job):
    if not job.get("again"):
        return ""
    s = "他之前让你做的：「" + " / ".join(job.get("before") or []) + "」"
    if job.get("file"):
        s += f"；你做了「{job['file']}」" + (f"（做的是：{job['what']}）" if job.get("what") else "")
    return s + "。\n"


def ack_block(job):
    if job.get("again"):
        fix = ""
        if job.get("fix"):
            fix = ("他说的那几处毛病：对的就认（说一句你改哪几处就行），他说错的也可以笑着顶一句。"
                   + ("他在问改好的文件 —— 你还没改（交货以后没发过新文件）：老实说还没弄好、这就改。" if job.get("where") else ""))
        return (f"\n# 他要你把刚才做的{job['kind']}改了 / 重做\n" + _prev(job) +
                ("他后来说的：「" + " / ".join(job.get("said") or [])[-200:] + "」\n" if job.get("fix") else "") +
                "你接了。这一条只用你自己的口气回一两句：认了就认了（是他说错了也行 笑着说一句）、答应现在就重新弄 —— "
                "别推到「明早」「睡醒」「待会儿」：系统马上就会让你重新做完整 存成文件发给他。" + fix +
                f"{TYPO}（顺嘴说一句你按哪个做）。别贴代码、别说「改好了」（还没改）、别编你之前画了什么。状态行里别写 do。\n")
    return (f"\n# 他让你做个{job['kind']}（是个大活）\n"
            "你接了。这一条只用你自己的口气回一两句：可以嘟囔两句麻烦、讨价还价着玩，"
            "但要答应、说你这就去弄 —— 别反问「要不要现在写」、别推到明天、别贴代码、别说「写好了」（还没写）。"
            f"{TYPO}（拿不准是哪个就顺嘴确认一句）。" + _can_line(job) +
            "系统接着就会让你把它做完整 存成文件发给他。状态行里别写 do。\n")


PDF_BROWSER = ""           # config make.pdf_browser（bot 开机时填）：打印 PDF 用哪个浏览器 空 = 自己找 Edge / Chrome


def is_doc(job):
    ext = str((job or {}).get("ext") or "")
    return ext == "auto" or ext in docmaker.EXTS


def _can(job=None):
    """这台电脑现在能做的文件格式（她别再说「docx我生不出来」）"""
    have = docmaker.available(PDF_BROWSER)
    return "、".join(docmaker.NAMES[e] for e in have) + "、网页（html）、各种代码文件"


def _can_line(job):
    if not is_doc(job):
        return ""
    return f"能做成真文件的格式：{_can(job)} —— 不只是网页。"


# 是在画东西（SVG / canvas / 动画）：讲究画得对不对 —— 交之前照 DRAW_RULES 查、再自己检查一遍（10-28 v1.9.1）
DRAW = re.compile(r"画|svg|canvas|动画|插画|插图", re.I)
DRAW_RULES = (
    "- 画东西（SVG / canvas）要注意 —— 交之前照这几条从头查一遍：\n"
    "  · 每个部件放对地方、该挨着的挨上：脚踩在脚踏上、屁股坐在车座上、手 / 翅膀搭在车把上、嘴里叼的东西在嘴里 —— 把坐标算一遍\n"
    "  · 会转 / 会缩放的东西：SVG 元素用 CSS 的 rotate / scale 动画 一定给它写 transform-box: fill-box; transform-origin: center;"
    "（或者用 <animateTransform> 把绕哪个点转写明）—— 不写的话它绕整张图的左上角转 东西就飞走了\n"
    "  · 连在一起的部件放在同一个 <g> 里一起动（嘴和嘴里的鱼、脚和脚踏）；脚踏绕中轴转 脚跟着脚踏走。"
    "做不好的动画就别做 —— 不动但对得上 比动起来散架强\n"
    "  · 别画说不清是什么的东西（多出来的圆圈、线条）；画面里每样东西都要认得出是什么\n")
OLD_MAX = 40000            # 重做时上一个文件多大以内整个给她看


def is_draw(job, said=None):
    """这活是在画东西？（种类是网页 / SVG / 小游戏、他说的里面有「画 / svg / canvas / 动画」）"""
    if (job or {}).get("kind") not in ("网页", "SVG 图", "小游戏"):
        return False
    t = " ".join(str(x) for x in list(said or []) + list((job or {}).get("before") or []) + [(job or {}).get("what") or ""])
    return bool(DRAW.search(t)) or (job or {}).get("kind") == "SVG 图"


def make_block(job, said, old=""):
    """真做的那一轮；old = 重做时上一个文件的完整代码（10-28 v1.9.1：以前重做看不到旧的 他说的毛病没法照着改）"""
    s = " / ".join(str(x)[:300] for x in (said or []) if str(x).strip())[:900]
    if job.get("again"):
        head = f"\n# 现在把他要的{job['kind']}重新做好\n" + _prev(job) + f"现在他说：「{s}」\n"
        if old and len(old) <= OLD_MAX:
            head += ("你上次发给他的那个文件 完整代码在下面。\n"
                     "- 他说了哪几处不对 → 在代码里找到是哪几行造成的 改对；别的好的地方原样留着\n"
                     "- 他是要换个东西 / 换个样子 → 照他说的重新做\n"
                     "- 他只说「有问题」没说哪 → 自己从头查一遍 把毛病找出来改掉\n"
                     "不管哪种 都输出改好以后的完整文件（不是只说改了哪）\n"
                     f"```\n{old}\n```\n")
        else:
            head += "照他现在说的重新做一个完整的（不是在旧的上面打补丁、也不是只说改了哪）\n"
    else:
        head = f"\n# 现在把他要的{job['kind']}做出来\n他说的：「{s}」\n"
    if is_doc(job):
        return head + _doc_rules(job) + f"- {TYPO}；你刚才回他的那句里说了按哪个做 就照那个\n" + _TAIL
    return (head +
            "- 认真做、做完整、细节做足：他要什么就给什么 别缩水别偷懒。网页 / 小游戏：一个 html 文件 CSS 和 JS 都写在里面 "
            "不引外部文件和链接、断网也能直接打开；要画东西的用 SVG 或 canvas 自己画（别用图片链接）\n"
            + (DRAW_RULES if is_draw(job, said) else "") +
            f"- {TYPO}；你刚才回他的那句里说了按哪个做 就照那个\n"
            "- 一个 ``` 代码块装整个文件（从第一行到最后一行 别省略）\n" + _TAIL)


def _doc_rules(job):
    """Word / PDF / Excel / PPT / Markdown / 文本：她写内容（Markdown / 表格）系统转成真文件（11-02 v1.17.4）"""
    ext = str(job.get("ext") or "")
    have = docmaker.available(PDF_BROWSER)
    L = []
    if ext == "auto":
        L.append(f"- 他没说要什么格式：你按内容挑最合适的一种。这台电脑能做：{_can(job)}。"
                 "在代码块开头写你挑的格式（```docx / ```pdf / ```xlsx / ```pptx / ```md / ```txt / ```html）；"
                 "挑了网页就照网页的规矩：一个 html 文件 CSS 和 JS 都写在里面\n")
        name = "文件"
    else:
        name = docmaker.NAMES.get(ext, "文档")
        L.append(f"- 这次交的是{name}（{ext}）：系统会把你写的转成真正的{name}发给他（不是网页）\n")
        if ext not in have:
            alt = "每页一张的 PDF" if ext == ".pptx" and ".pdf" in have else "Word 文档"
            L.append(f"- 这台电脑现在做不了{name}：照样写 系统会做成{alt}（交货那句照实说是{alt}）\n")
    L.append("- 内容用 Markdown 写在一个 ``` 代码块里：第一行「# 标题」、小标题 ##、列表用 -、"
             "表格用 | 列 | 列 |（第二行 |---|---|）、要强调的用 **…**；代码块里别再套 ```\n")
    if ext in (".xlsx", ".csv"):
        L.append("- 表格：用 Markdown 表格写 第一行是表头；要分几张表 就在每张前面写「## 表名」；数字只写数字（单位写进表头）\n")
    if ext == ".pptx":
        L.append("- 幻灯片：第一行「# 总标题」；每一页用「## 这页的标题」开头 下面几条 - 要点（一页别超过 6 条）\n")
    L.append("- 认真写、写完整：他要什么就写什么 别缩水；总结聊天就照聊天记录里真有的写（谁说了什么） 别编；不知道的就写不知道\n")
    return "".join(L)


_TAIL = ("- 输出格式：代码块前面什么都别写；"
            "代码块后面另起一行 用你自己的口气说一句交货的话 30 字以内 "
            "—— 东西已经做好了 别再说「这就来」「马上画」；再另起一行写「做的是：…」20 字以内 说清楚画了 / 做了什么"
            " —— 这行不发给他 是让你以后记得自己做了啥\n"      # （11-11 v1.17.38 E1：不给现成的话） 以前给了一条示范内容
            "- 别解释代码 / 内容、别分段讲、别说「复制下面这段」—— 系统会把它存成文件发给他\n"
            "- 最后照常写 %%STATE%% 那一行\n")


REVIEW_SYS = "你做网页、SVG 很多年了，交东西之前会自己仔细检查一遍。只按要求输出。"


def review_block(job, said, code, what=""):
    """做完、发之前自己查一遍（10-28 v1.9.1：鹈鹕的鱼和水波圈绕左上角转飞走了、脚踏不动脚悬空）"""
    s = " / ".join(str(x)[:300] for x in list((job or {}).get("before") or []) + list(said or []) if str(x).strip())[:900]
    return (f"下面是你刚做好、还没发出去的{job['kind']}。他要的是：「{s}」" + (f"（你做的是：{what}）" if what else "") + "\n"
            "发之前从头仔细查一遍：\n" + DRAW_RULES +
            "- 代码能直接打开：标签都闭合、JS 没语法错、不引外部文件和链接\n"
            "- 他要的东西都在、一眼认得出是什么\n"
            "查出毛病就改好 输出改好以后的完整文件：一个 ``` 代码块 从第一行到最后一行 别省略；"
            "一处毛病都没有 就只回「没问题」三个字。别解释、别说别的。\n\n"
            f"```html\n{code}\n```\n")


def split(text):
    """→ ([(lang, code, closed)], 剩下的话)。没有 ``` 但整段是网页（<!DOCTYPE html> … </html>）也算一块"""
    text = str(text or "")
    blocks, rest, last = [], [], 0
    for m in FENCE.finditer(text):
        rest.append(text[last:m.start()])
        blocks.append((m.group(1).strip().lower(), m.group(2).rstrip(), m.group(3).strip() == "```"))
        last = m.end()
    rest.append(text[last:])
    if not blocks:
        # 没包 ``` 的整页网页：行首就是 <!DOCTYPE html / <html、里面有 <head> 或 <body>、以 </html> 收尾
        # （整段回复就是这一页、被截断了没收尾的也算）—— 10-28 审查 reply-13：以前纯文字长篇讲 HTML 提到「<html>」
        #   后面又够长 整段讲解就被当成网页存成文件发走 聊天里只剩「网页最外面那层是」
        m = re.search(r"(?:^|\n)[ \t]*(<!DOCTYPE html.*?(?:</html>|\Z)|<html[\s>].*?(?:</html>|\Z))", text, re.S | re.I)
        # 前面先说了一句「好嘞 给你」、后面的网页被截断了：有好几个收尾标签（</title> </style> </div>…）的是真网页 照样拿走
        # （讲 HTML 的话里一般没有；10-28 审查复查 reply-13：以前这种半截网页整段贴进聊天）
        if m and not (re.search(r"<head[\s>]|<body[\s>]", m.group(1), re.I)
                      and (m.group(1).lower().rstrip().endswith("</html>") or not text[:m.start(1)].strip()
                           or len(re.findall(r"</[a-z]", m.group(1), re.I)) >= 3)):
            m = None
        if not m:
            m = re.search(r"(<svg[\s>].*?(?:</svg>|\Z))", text, re.S | re.I)
        if m and big(m.group(1)):
            code = m.group(1)
            closed = code.lower().rstrip().endswith(("</html>", "</svg>"))
            return [("html" if "<svg" not in code[:20].lower() else "svg", code.strip(), closed)], \
                (text[:m.start(1)] + text[m.end(1):]).strip()
    joined = "".join(rest)
    joined = re.sub(r"[ \t]*\n[ \t]*\n[ \t\n]*", "\n", joined)
    return blocks, joined.strip()


def pull_big(text):
    """回复里够长的代码块拿出来（存成文件发）；短的留在原处照旧贴 → ([(lang, code, closed)], 剩下的话)"""
    text = str(text or "")
    if "```" not in text and not re.search(r"<!DOCTYPE html|<html[\s>]|<svg[\s>]", text, re.I):
        return [], text
    out, keep, last = [], [], 0
    for m in FENCE.finditer(text):
        if big(m.group(2)):
            keep.append(text[last:m.start()])
            out.append((m.group(1).strip().lower(), m.group(2).rstrip(), m.group(3).strip() == "```"))
            last = m.end()
    keep.append(text[last:])
    rest = "".join(keep)
    if not out and "```" not in text:
        blocks, rest2 = split(text)            # 没有 ``` 的整段网页 / SVG
        if blocks:
            return blocks, rest2
    rest = re.sub(r"[ \t]*\n[ \t]*\n[ \t\n]*", "\n", rest).strip()
    return out, rest


def big(code):
    code = str(code or "")
    return code.count("\n") + 1 >= BIG_LINES or len(code) >= BIG_CHARS


def ext_of(lang, code, dflt=""):
    lang = (lang or "").strip().lower()
    head = str(code or "")[:400].lower()
    if lang in ("xml", "html", "") and "<svg" in head and "<html" not in head and "<!doctype" not in head:
        return ".svg"
    if lang in LANG_EXT:
        return LANG_EXT[lang]
    if "<!doctype html" in head or "<html" in head:
        return ".html"
    if "<svg" in head:
        return ".svg"
    if re.search(r"^\s*(?:import |from \w+ import |def |class \w+[:(]|print\()", str(code or ""), re.M):
        return ".py"
    return dflt or ".txt"


FMT = {"docx": ".docx", "doc": ".docx", "word": ".docx", "pdf": ".pdf", "xlsx": ".xlsx", "xls": ".xlsx", "excel": ".xlsx",
       "csv": ".csv", "pptx": ".pptx", "ppt": ".pptx", "md": ".md", "markdown": ".md", "txt": ".txt", "text": ".txt"}


def target_ext(lang, code, job=None):
    """这次交什么格式（11-02 v1.17.4）：要的是文档 → 就那个格式；没说格式（auto）→ 看她在代码块开头写的；代码照旧看语言"""
    want_ = str((job or {}).get("ext") or "")
    lg = (lang or "").strip().lower()
    if want_ in docmaker.EXTS:
        return want_
    if want_ == "auto":
        if lg in FMT:
            return FMT[lg]
        e = ext_of(lg, code, "")
        return e if e and e != ".txt" else ".docx"
    return ext_of(lang, code, want_)


def _clean(name):
    name = BAD_NAME.sub("", str(name or "")).strip().strip(".")
    return re.sub(r"\s+", " ", name)[:30].strip()


def file_name(job, code, ext, now=None):
    """网页 / SVG 有 <title> 就用它；不然第一行注释；再不然「种类 月日-时分」"""
    code = str(code or "")
    m = re.search(r"<title[^>]*>(.*?)</title>", code[:3000], re.S | re.I)
    base = _clean(m.group(1)) if m else ""
    if not base:
        m = re.search(r"^\s*(?:#|//|--|<!--)\s*([^\n]{2,40}?)\s*(?:-->)?\s*$", code[:400], re.M)
        if m and not m.group(1).startswith(("!", "-*-", "coding", "!/")):
            base = _clean(m.group(1))
    if not base:                           # 时间用北京时间 跟按北京日期分的文件夹一致（10-28 审查 reply-13：以前是电脑本地的英国时间）
        kind = (job or {}).get("kind") or "代码"
        base = _clean(kind.replace(" ", "")) + clock.strf("_%m%d-%H%M", now or time.time())
    return base + ext


def save(folder, name, code):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    p = folder / name
    stem, suf, i = p.stem, p.suffix, 2
    while p.exists():
        p = folder / f"{stem}-{i}{suf}"
        i += 1
    p.write_text(str(code).rstrip() + "\n", encoding="utf-8", newline="\n")
    return p


def chunks(code, size=2800, most=4):
    """文件发不出去时 退回成几条文字（每条 size 个字以内 最多 most 条）
    一行就比一条还长（压成一行的 js / 整行的 svg path）：硬切开（10-28 Codex #11：以前整行塞进一条 QQ 发不出去）"""
    code = str(code or "")
    out, cur = [], ""
    for line in code.splitlines(keepends=True):
        while len(line) > size:
            if cur:
                out.append(cur)
                cur = ""
            out.append(line[:size])
            line = line[size:]
        if len(cur) + len(line) > size and cur:
            out.append(cur)
            cur = ""
        cur += line
    if cur:
        out.append(cur)
    return out[:most], len(out) > most


class Quota:
    """每人每天最多做几个、总共每天最多几个（老板不限）；存 data/maker.json"""

    def __init__(self, path, per_user=6, per_day=40):
        self.path, self.per_user, self.per_day = Path(path), int(per_user), int(per_day)

    def _load(self, day):
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            d = {}
        if not isinstance(d, dict) or d.get("day") != day:
            d = {"day": day, "n": 0, "users": {}}
        d.setdefault("users", {})
        return d

    def allow(self, uid, owner, day):
        if owner:
            return True, ""
        if self.per_user <= 0 or self.per_day <= 0:
            return False, "这个现在只给老板做"
        d = self._load(day)
        n_all, n_me = int(d.get("n", 0)), int(d["users"].get(str(uid), 0))
        if n_all >= self.per_day:
            return False, f"今天已经做了 {n_all} 个（每天最多 {self.per_day} 个）"
        if n_me >= self.per_user:
            return False, f"今天已经给他做了 {n_me} 个（每人每天最多 {self.per_user} 个）"
        return True, ""

    def give_back(self, uid, day):
        """接下来时先算上的那个 没做成 / 不做了（他又改了）→ 还回去（10-28 Codex #7）；过了一天就不还了"""
        if not day:
            return
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            return
        if not isinstance(d, dict) or d.get("day") != day:
            return
        d["n"] = max(0, int(d.get("n", 0)) - 1)
        users = d.setdefault("users", {})
        users[str(uid)] = max(0, int(users.get(str(uid), 0)) - 1)
        self._save(d)

    def _save(self, d):
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
            tmp.replace(self.path)
        except Exception:
            pass

    def took(self, uid, day):
        d = self._load(day)
        d["n"] = int(d.get("n", 0)) + 1
        d["users"][str(uid)] = int(d["users"].get(str(uid), 0)) + 1
        self._save(d)
        return d["n"]
