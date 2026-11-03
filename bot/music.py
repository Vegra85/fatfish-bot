#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
现找现唱（10-26 小明：点了她不会的歌 去网易云/B站找完全对得上的原曲 截 10 来秒高潮唱一下）
  网易云：搜歌 → 歌名要完全对上（点了歌手的 歌手也要对上）、不要伴奏/翻唱/DJ 版
          → 官方外链能播就下（VIP 歌、只给大陆播的歌放不了 —— 不绕过 直接去 B站）
          → 歌词时间轴里重复最多的那几句 = 副歌 从那开始截
  B站：搜「歌名 歌手」→ 标题对得上、不是翻唱/伴奏/教学/合集的视频里挑播放最多的 → 音轨 → 按响度找最响的一段
  截 sec 秒（默认 12）→ 24k 单声道 wav（头尾淡入淡出）；整首下下来的音频剪完就删 只留这一小段
  找过的记在 data/song_online.json：下次点同一首直接用（不用再下）
10-29 隔夜（第 8 件）选歌先核对标题：07:13「茉莉花」选成了 Ленинград 的俄语 MV（标题「【中俄字幕】…动画MV Ленинград -Жу-Жу（JU JU）茉莉花等超多中国元素」
  只是顺带提到歌名）。现在 B站 搜到的每一条都过 check_title：标题里要有歌名（繁简 / 空格 / 大小写 / 括号里的英文名都认）、不能是 混剪 / MV / 字幕组 / 动画 /
  合集 / 翻唱合集 这类、歌名不能只是顺带提到；带 原唱 / 官方 / live / 歌词版 的排前面；一条都对不上 → 不唱（机器人那边补一句「没找着」）。
  下载下来量真实时长，不到 MIN_REAL_SEC（80）秒的当试听片段不用。记过的旧结果下次点的时候也重新核对一遍（核对不过就丢掉重找）。
  日志 [唱歌] 写明选的是哪条、为什么选它、核对掉了哪几条。
11-01 v1.16.0 多几个来源 + 链接点歌 + 拿不准就问（接口约定见 CONTRACT_1101 的「A：music.py」）
  来源：QQ 音乐 / 网易云 / 酷狗 / B站 / YouTube，顺序照 config sing.sources（默认 qq → netease → kugou → bili → youtube）；
  QQ 音乐、酷狗要主人扫码登录（musiclogin.cookies）才用，网易云登录了能放他账号有权限的歌、没登录照旧只放免费的；
  YouTube 用 yt-dlp（可选，没装 / 没 Deno 就跳过）。歌名是外文的 YouTube 提到 B站 前面。
  VIP / 地区限制不绕：主人的账号能放才放，放不了照实记原因、换下一个来源。
  这几个平台的接口这台写代码的机器连不上 —— 凭知识写的，解析都宽容（字段缺了不报错）；主人电脑上跑 selftest() 看哪个通哪个不通。
  新来源的网络请求都走 _http / _download / _yt_extract 三个函数（测试替换这三个就不联网；B站 还是 bili2 的客户端 + bili_search / bili_audio）。
  链接点歌：find_url 认链接，fetch_url 直接下那一个（歌曲平台下不了就读出歌名歌手再按名字找）。
  拿不准就问：fetch(ask=True) 时同名歌没说歌手、前几名拉不开 → 返回 {"ask": …} 让她问一句；fetch_pick 下选中的那个。
  规矩 E1：这里只返回状态 / 原因，她要说的话让模型自己说。
11-01 v1.16.5 唱整首：with full_mode(上限秒数): fetch / fetch_pick / fetch_url → 拿到完整音频后不剪（_make_full：只去头尾静音、超了截到上限
  最后 3 秒淡出）；记在 song_online.json 的「full|…」下、文件叫 full_net_*.wav（跟剪好的一段分开 不会混）
"""
import asyncio
import hashlib
import html
import json
import os
import random
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import httpx

ROOT = Path(__file__).resolve().parent
CLIP_DIR = ROOT / "data" / "voice"
TMP_DIR = ROOT / "data" / "song_tmp"
CACHE = ROOT / "data" / "song_online.json"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36")
NE_HEADERS = {"User-Agent": UA, "Referer": "https://music.163.com/", "Cookie": "appver=2.9.11; os=pc"}
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)
MAX_BYTES = 40 * 1024 * 1024
# 不是原曲的版本（点歌的人自己写了这些词的除外）
# 11-01 v1.15.18：补 karaoke / off vocal / 消音 / 无人声 / 伴唱（伴奏送去换声只会出空白）、深情版 / 童声版 / 8D / 抖音版；
#   「dj」以前写 \bdj\b —— 「DJ版」里「版」也算字 \b 不成立 漏了；改成前后不是英文字母
NOT_ORIG = re.compile(r"伴奏|伴唱|纯音乐|karaoke|off\s*vocal|消音|无人声|深情版|童声版|抖音版|(?<![a-z])8d(?![a-z])|"
                      r"\binst\b|instrumental|翻唱|\bcover\b|翻自|(?<![a-z])dj(?![a-z])|remix|改编|教学|吉他|钢琴|尤克(?:里){2}|口琴|小提琴|"
                      r"架子鼓|指弹|弹唱|合集|串烧|混剪|鬼畜|reaction|片段|\bcut\b|舞蹈|翻跳|练习|试唱|ai翻唱|ai版|ai cover|模仿|"
                      r"女声版|男声版|降调|升调|加速|倍速|慢速|铃声|教程|循环|1小时|一小时|10小时|"
                      # 11-01 v1.16.0 YouTube 的英文标题：sped up / slowed / nightcore / reverb / bass boosted / 1 hour 都不是原曲
                      r"sped\s*up|speed\s*up|slowed|nightcore|(?<![a-z])reverb(?![a-z])|bass\s*boost|"
                      r"(?<![a-z0-9])(?:1|10)\s*hours?(?![a-z])|piano\s*version|(?<![a-z])lo-?fi(?![a-z])", re.I)
# 10-29 第 8 件：小明 的清单——排除 混剪 / MV / 字幕组 / 动画 / 合集 / 翻唱合集；优先 原唱 / 官方 / live / 歌词版
# （英文词前后不能紧挨着别的字母；中文紧挨着不算「挨着」：「官方MV」「动画MV」里的 MV 要抓到）
BAD_VIDEO = re.compile(r"混剪|(?<![a-z])a?mv(?![a-z])|字幕组|[中日韩英俄]{1,2}字幕|双语字幕|动画|动漫|番剧|合集|串烧|盘点|大全|歌单|二创|手书|reaction|解说|(?<![a-z])mad(?![a-z])", re.I)
PREFER_STRONG = re.compile(r"原唱|官方|official|(?<![a-z])live(?![a-z])|现场|演唱会|歌词版|动态歌词|(?<![a-z])lyrics?(?![a-z])", re.I)
PREFER = re.compile(r"高音质|无损|hi-?res|完整版|录音棚|audio|音乐(?!会|节|剧)", re.I)   # 「音乐会」不算（10-31 v1.15.15 小饭桌音乐会被当成「带音乐」）
LIVE = re.compile(r"(?<![a-z])live(?![a-z])|现场|演唱会|音乐会|小饭桌|小桌|tiny\s*desk|不插电|unplugged|直播|livehouse|音乐节|"
                  r"(?<![a-z])concert(?![a-z])", re.I)    # concert：YouTube 英文标题（11-01 v1.16.0）   # 换声开着时现场版往后排（观众声 / 混响 分不干净 换出来更糊）10-31 v1.15.14
CUT_V = 2                     # 剪法版本：10-31 v1.15.14 找副歌 + 对齐句子；记过的旧片段（最响的 12 秒）不再用 重新剪
MIN_REAL_SEC = 80             # 下载下来量到的真实时长不到这个数 = 试听片段 / 预览 → 不用
MAX_LEFTOVER_ASCII = 40       # 英文 / 数字多于这个数也算「别的内容」
MAX_LEFTOVER = 10             # 标题里除了歌名 / 歌手 / 装饰词 / 括号，剩下的汉字 / 外文字母多于这个数 = 歌名只是顺带提到（歌名在《》里的除外）
# 10-26 晚：电脑不在中国大陆时 网易云对海外 IP 的搜索只回一串加密的字（{"abroad": true, "result": "35b1…"}）
# 以前把这串字当歌单读 → AttributeError 整个唱歌出错、她只说了句「咳咳」。不去破解它：认出来就直接走 B站 6 小时后再试
ABROAD_HOLD = 6 * 3600
BILI_TRY = 3                  # B站 第一个视频放不了（地区限制/拿不到音轨）就换下一个 最多试这么多个
_NE = {"abroad_until": 0.0, "told": 0.0, "why": ""}

# 11-01 v1.16.0 来源（key 给配置 / 选项用，中文名进 meta 的 "source"、日志：以前记过的 "网易云" / "B站" 照旧认）
SRC_NAME = {"qq": "QQ音乐", "netease": "网易云", "kugou": "酷狗", "bili": "B站", "youtube": "YouTube"}
SRC_KEY = {v: k for k, v in SRC_NAME.items()}
DEFAULT_SOURCES = ["qq", "netease", "kugou", "bili", "youtube"]
SOURCES = None                # 不是 None 就用这个顺序（测试 / 临时改用；平时读 config sing.sources）
SONG_SRC = ("qq", "netease", "kugou")      # 歌曲平台：有歌名歌手、有歌词；B站 / YouTube 是视频
SONG_TRY = 4                  # 歌曲平台前几首都试（第一首 VIP 放不了换下一首）
_TOLD = {"skip": ""}


def ne_abroad():
    """网易云这会儿是不是把这台电脑当海外 IP（只给加密的结果 用不了）"""
    return time.time() < _NE["abroad_until"]


def _norm(t):
    t = str(t or "").lower()
    t = re.sub(r"[（(【\[].*?[)）】\]]", "", t)                     # 括号里的（Live / 电影插曲 / 伴奏…）
    return re.sub(r"[\s《》<>「」『』\"'“”‘’·・,，.。!！?？~～\-_/|:：&+、]", "", t)


def parse(req):
    """「雨爱 - 杨丞琳」「杨丞琳的雨爱」「《雨爱》杨丞琳」→ (歌名, 歌手)"""
    r = str(req or "").strip()
    m = re.match(r"^\s*《([^《》]+)》\s*(.*)$", r)
    if m:
        return m.group(1).strip(), re.sub(r"^[-—|｜\s的]+", "", m.group(2)).strip()
    for sep in (" - ", " — ", "－", "—", " -", "- ", "|", "｜", "/"):
        if sep in r:
            a, b = r.split(sep, 1)
            return a.strip(), b.strip()
    m = re.match(r"^(.{1,8}?)的(.{1,30})$", r)
    if m and not re.search(r"(这|那|一|哪)", m.group(1)):
        return m.group(2).strip(), m.group(1).strip()
    return r, ""


def _ok_name(name, want, raw_req):
    """歌名完全对得上（去掉括号里的 Live/伴奏 之类再比）、而且不是伴奏翻唱版"""
    if _norm(name) != _norm(want):
        return False
    extra = str(name or "")
    if re.search(r"[（(【\[]\s*原唱", extra) and not re.search(r"原唱", raw_req or ""):
        return False                       # 网易云「晴天（原唱 周杰伦）」是别人唱的（11-01 v1.15.18；B站标题里的「原唱」是加分词 不走这里）
    return not (NOT_ORIG.search(extra) and not NOT_ORIG.search(raw_req or ""))


def _artist_ok(names, artist):
    if not artist:
        return True
    a = _norm(artist)
    return any(a and (a in _norm(n) or _norm(n) in a) for n in names if _norm(n))

# ───────────── 标题核对（10-29 第 8 件）─────────────
# 繁体 → 简体（只用来比较：把视频标题和点歌的歌名都转成简体再比）。字表来自 OpenCC（Apache-2.0）的 TSCharacters，只留常用汉字的一对一
_T2S_TRAD = "丟並乾亂亙亞佇佈佔併來侖侶侷俁係俔俠俥俬倀倆倈倉個們倖倫偉側偵偽傑傖傘備傢傭傯傳傴債傷傾僂僅僉僑僕僞僥僨僱價儀儁儂億儈儉儎儐儔儕儘償優儲儷儺儻儼兇兌兒兗內兩冊冑冪凈凍凜凱別刪剄則剋剎剗剛剝剮剴創剷劃劇劉劊劌劍劑勁動務勛勝勞勢勩勱勳勵勸勻匭匯匱區協卹卻卽厙厠厤厭厲厴參叄叢吒吳吶呂咼員唄唸問啓啞啟啢喚喪喫喬單喲嗆嗇嗊嗎嗚嗩嗶嘆嘍嘓嘔嘖嘗嘜嘩嘮嘯嘰嘵嘸嘽噁噓噝噠噥噦噯噲噴噸噹嚀嚇嚌嚐嚕嚙嚥嚦嚨嚮嚲嚳嚴嚶囀囁囂囅囈囉囌囑囪圇國圍園圓圖團垻埡埰執堅堊堖堝堯報場塊塋塏塒塗塚塢塤塵塹墊墜墮墰墳墶墻墾壇壋壎壓壘壙壚壜壞壟壠壢壩壪壯壺壼壽夠夢夥夾奐奧奩奪奬奮奼妝姍姦娛婁婦婭媧媯媼媽嫋嫗嫵嫺嫻嫿嬀嬃嬈嬋嬌嬙嬡嬤嬪嬰嬸孃孌孫學孿宮寀寢實寧審寫寬寵寶將專尋對導尷屆屍屓屜屢層屨屬岡峯峴島峽崍崑崗崙崢崬嵐嵗嶁嶄嶇嶔嶗嶠嶢嶧嶨嶮嶸嶺嶼嶽巋巒巔巖巰巹帥師帳帶幀幃幗幘幟幣幫幬幹幾庫廁廂廄廈廎廕廚廝廟廠廡廢廣廩廬廳弒弔弳張強彆彈彌彎彔彙彠彥彫彲彿後徑從徠復徵徹恆恥悅悞悵悶悽惡惱惲惻愛愜愨愴愷愾慄態慍慘慚慟慣慤慪慫慮慳慶慼慾憂憊憐憑憒憖憚憤憫憮憲憶懇應懌懍懞懟懣懨懲懶懷懸懺懼懾戀戇戔戧戩戰戱戲戶拋挩挱挾捨捫捱捲掃掄掗掙掛採揀揚換揮揯損搖搗搵搶摑摜摟摯摳摶摺摻撈撏撐撓撟撣撥撫撲撳撻撾撿擁擄擇擊擋擔據擠擣擬擯擰擱擲擴擷擺擻擼擾攄攆攏攔攖攙攛攜攝攢攣攤攪攬敎敓敗敘敵數斂斃斆斕斬斷於旂旣昇時晉晝暈暉暘暢暫曄曆曇曉曏曖曠曨曬書會朧朮東枴柵柺査桿梔梘條梟梲棄棊棖棗棟棧棲棶椏楊楓楨業極榘榦榪榮榲榿構槍槓槤槧槨槮槳槶槼樁樂樅樑樓標樞樣樧樳樸樹樺樿橈橋機橢橫檁檉檔檜檟檢檣檮檯檳檸檻櫃櫓櫚櫛櫝櫞櫟櫥櫧櫨櫪櫫櫬櫱櫳櫸櫻欄欅權欏欒欖欞欽歎歐歟歡歲歷歸歿殘殞殤殫殭殮殯殲殺殻殼毀毆毿氂氈氌氣氫氬氳氾汎汙決沒沖況泝洩洶浹涇涗涼淒淚淥淨淩淪淵淶淺渙減渢渦測渾湊湞湧湯溈準溝溫溮溳溼滄滅滌滎滙滬滯滲滷滸滻滾滿漁漊漚漢漣漬漲漵漸漿潁潑潔潙潛潤潯潰潷潿澀澆澇澐澗澠澤澦澩澮澱濁濃濕濘濚濛濜濟濤濫濰濱濺濼濾瀂瀅瀆瀉瀋瀏瀕瀘瀝瀟瀠瀦瀧瀨瀰瀲瀾灃灄灑灕灘灝灣灤灧灩災為烏烴無煉煒煙煢煥煩煬熅熒熗熱熲熾燁燈燉燒燙燜營燦燬燭燴燻燼燾爍爐爛爭爲爺爾牀牆牘牽犖犛犢犧狀狹狽猙猶猻獁獃獄獅獎獨獪獫獮獰獲獵獷獸獺獻獼玀現琱琺琿瑋瑒瑣瑤瑩瑪瑲璉璡璣璦璫環璵璸璽璿瓊瓏瓔瓚甌甕產産甦甯畝畢畫異畵當疇疊痙痠痾瘂瘋瘍瘓瘞瘡瘧瘮瘲瘺瘻療癆癇癉癒癘癟癡癢癤癥癧癩癬癭癮癰癱癲發皁皚皰皸皺盃盜盞盡監盤盧盪眞眥眾睏睜睞瞘瞞瞶瞼矇矓矚矯硃硜硤硨硯碕碩碭碸確碼磑磚磠磣磧磯磽礄礆礎礙礦礪礫礬礱祕祿禍禎禕禡禦禪禮禰禱禿秈稅稈稜稟種稱穀穌積穎穠穡穢穩穫穭窩窪窮窯窵窶窺竄竅竇竈竊竪競筆筍筧箇箋箏節範築篋篔篠篤篩篳簀簍簑簞簡簣簫簹簽簾籃籌籙籛籜籟籠籤籩籪籬籮籲粵糉糝糞糧糰糲糴糶糹糾紀紂約紅紆紇紈紉紋納紐紓純紕紖紗紘紙級紛紜紝紡紮細紱紲紳紵紹紺紼紿絀終絃組絆絎結絕絛絝絞絡絢給絨絰統絲絳絶絹綁綃綆綈綉綌綏綑經綜綞綠綢綣綫綬維綯綰綱網綳綴綵綸綹綺綻綽綾綿緄緇緊緋緑緒緓緔緗緘緙線緝緞締緡緣緦編緩緬緯緱緲練緶緹緻緼縈縉縊縋縐縑縕縗縛縝縞縟縣縧縫縭縮縱縲縴縵縶縷縹總績繃繅繆繒織繕繚繞繡繢繩繪繫繭繮繯繰繳繹繼繽繾纇纈纊續纍纏纓纔纖纘纜缽罈罌罎罰罵罷羅羆羈羋羣羥羨義羶習翫翬翹翽耬耮聖聞聯聰聲聳聵聶職聹聽聾肅脅脈脛脣脩脫脹腎腖腡腦腫腳腸膃膕膚膠膩膽膾膿臉臍臏臘臚臟臠臢臥臨臺與興舉舊舘艙艤艦艫艱艷芻苧茲荊莊莖莢莧華菴菸萇萊萬萴萵葉葒葤葦葯葷蒐蒓蒔蒕蒞蒼蓀蓆蓋蓮蓯蓴蓽蔔蔘蔞蔣蔥蔦蔭蕁蕆蕎蕒蕓蕕蕘蕢蕩蕪蕭蕷薀薈薊薌薑薔薘薟薦薩薴薹薺藍藎藝藥藪藴藶藹藺蘀蘄蘆蘇蘊蘋蘚蘞蘢蘭蘺蘿虆處虛虜號虧虯蛺蛻蜆蝕蝟蝦蝨蝸螄螞螢螻螿蟄蟈蟎蟣蟬蟯蟲蟶蟻蠁蠅蠆蠍蠐蠑蠔蠟蠣蠨蠱蠶蠻衆衊術衕衚衛衝袞裊裏補裝裡製複褌褘褲褳褸褻襇襉襏襖襝襠襤襪襬襯襲襴覈見覎規覓視覘覡覥覦親覬覯覲覷覺覽覿觀觴觶觸訁訂訃計訊訌討訐訒訓訕訖託記訛訝訟訣訥訩訪設許訴訶診註証詁詆詎詐詒詔評詖詗詘詛詞詠詡詢詣試詩詫詬詭詮詰話該詳詵詼詿誄誅誆誇誌認誑誒誕誘誚語誠誡誣誤誥誦誨說説誰課誶誹誼誾調諂諄談諉請諍諏諑諒論諗諛諜諝諞諡諢諤諦諧諫諭諮諱諳諶諷諸諺諼諾謀謁謂謄謅謊謎謐謔謖謗謙謚講謝謠謡謨謫謬謭謳謹謾譁證譎譏譖識譙譚譜譟譫譭譯議譴護譸譽譾讀讅變讋讎讒讓讕讖讚讜讞豈豎豐豔豬豶貓貝貞貟負財貢貧貨販貪貫責貯貰貲貳貴貶買貸貺費貼貽貿賀賁賂賃賄賅資賈賊賑賒賓賕賙賚賜賞賠賡賢賣賤賦賧質賫賬賭賴賵賺賻購賽賾贄贅贇贈贊贋贍贏贐贓贔贖贗贛贜赬趕趙趨趲跡踐踰踴蹌蹕蹟蹠蹣蹤蹺躂躉躊躋躍躑躒躓躕躚躡躥躦躪軀車軋軌軍軑軒軔軛軟軤軫軲軸軹軺軻軼軾較輅輇輈載輊輒輓輔輕輛輜輝輞輟輥輦輩輪輬輯輳輸輻輼輾輿轀轂轄轅轆轉轍轎轔轟轡轢轤辦辭辮辯農迴逕這連週進遊運過達違遙遜遞遠遡適遲遷選遺遼邁還邇邊邏邐郟郵鄆鄉鄒鄔鄖鄧鄭鄰鄲鄴鄶鄺酇酈醃醖醜醞醟醣醫醬醱釀釁釃釅釋釐釒釓釔釕釗釘釙針釣釤釦釧釩釵釷釹釺鈀鈁鈃鈄鈅鈈鈉鈍鈎鈐鈑鈒鈔鈕鈞鈡鈣鈥鈦鈧鈮鈰鈳鈴鈷鈸鈹鈺鈽鈾鈿鉀鉅鉆鉈鉉鉋鉍鉑鉕鉗鉚鉛鉞鉢鉤鉦鉬鉭鉳鉶鉸鉺鉻鉿銀銃銅銍銑銓銖銘銚銛銜銠銣銥銦銨銩銪銫銬銱銳銷銹銻銼鋁鋃鋅鋇鋌鋏鋒鋙鋝鋟鋣鋤鋥鋦鋨鋩鋪鋭鋮鋯鋰鋱鋶鋸鋼錁錄錆錇錈錏錐錒錕錘錙錚錛錟錠錡錢錦錨錩錫錮錯録錳錶錸錼鍀鍁鍃鍅鍆鍇鍈鍊鍋鍍鍔鍘鍚鍛鍠鍤鍥鍩鍬鍰鍵鍶鍺鍼鍾鎂鎄鎇鎊鎌鎔鎖鎘鎚鎛鎡鎢鎣鎦鎧鎩鎪鎬鎭鎮鎰鎲鎳鎵鎶鎸鎿鏃鏇鏈鏌鏍鏐鏑鏗鏘鏜鏝鏞鏟鏡鏢鏤鏨鏰鏵鏷鏹鏽鐃鐋鐐鐒鐓鐔鐘鐙鐝鐠鐦鐧鐨鐫鐮鐲鐳鐵鐶鐸鐺鐿鑄鑊鑌鑑鑒鑔鑕鑞鑠鑣鑥鑭鑰鑱鑲鑷鑹鑼鑽鑾鑿钁钂長門閂閃閆閈閉開閌閎閏閑閒間閔閘閡閣閤閥閨閩閫閬閭閱閲閶閹閻閼閽閾閿闃闆闇闈闊闋闌闍闐闒闓闔闕闖關闞闠闡闢闤闥陘陝陞陣陰陳陸陽隉隊階隕際隨險隯隱隴隸隻雋雖雙雛雜雞離難雲電霑霢霧霽靂靄靆靈靉靚靜靝靦靨鞏鞝鞦鞽韁韃韆韉韋韌韍韓韙韜韝韞韻響頁頂頃項順頇須頊頌頎頏預頑頒頓頗領頜頡頤頦頭頮頰頲頴頷頸頹頻頽顆題額顎顏顒顓顔願顙顛類顢顥顧顫顬顯顰顱顳顴風颭颮颯颱颳颶颸颺颻颼飀飄飆飈飛飠飢飣飥飩飪飫飭飯飱飲飴飼飽飾飿餃餄餅餈餉養餌餎餏餑餒餓餕餖餘餚餛餜餞餡館餬餱餳餵餶餷餺餼餾餿饁饃饅饈饉饊饋饌饑饒饗饜饞饢馬馭馮馱馳馴馹駁駐駑駒駔駕駘駙駛駝駟駡駢駭駰駱駸駿騁騂騅騌騍騎騏騖騙騤騫騭騮騰騶騷騸騾驀驁驂驃驄驅驊驌驍驏驕驗驚驛驟驢驤驥驦驪驫骯髏髒體髕髖髮鬆鬍鬚鬢鬥鬧鬨鬩鬮鬱鬹魎魘魚魛魢魨魯魴魷魺鮁鮃鮊鮋鮍鮎鮐鮑鮒鮓鮚鮜鮝鮞鮦鮪鮫鮭鮮鮳鮶鮺鯀鯁鯇鯉鯊鯒鯔鯕鯖鯗鯛鯝鯡鯢鯤鯧鯨鯪鯫鯰鯴鯷鯽鯿鰁鰂鰃鰈鰉鰍鰏鰐鰒鰓鰛鰜鰟鰠鰣鰥鰨鰩鰭鰮鰱鰲鰳鰵鰷鰹鰺鰻鰼鰾鱂鱅鱈鱉鱒鱔鱖鱗鱘鱝鱟鱠鱣鱤鱧鱨鱭鱯鱷鱸鱺鳥鳧鳩鳬鳲鳳鳴鳶鴆鴇鴉鴒鴕鴛鴝鴞鴟鴣鴦鴨鴯鴰鴴鴻鴿鵂鵃鵐鵑鵒鵓鵜鵝鵠鵡鵪鵬鵮鵯鵰鵲鵷鵾鶇鶉鶊鶓鶖鶘鶚鶡鶥鶩鶬鶯鶲鶴鶹鶺鶻鶼鶿鷀鷁鷂鷄鷊鷓鷖鷗鷙鷚鷥鷦鷫鷯鷲鷳鷴鷸鷹鷺鷽鸇鸌鸏鸕鸘鸚鸛鸝鸞鹵鹹鹺鹼鹽麗麥麩麪麫麯麴麵麼麽黃黌點黨黲黴黶黷黽黿鼂鼉鼕鼴齊齋齎齏齒齔齕齗齙齜齟齠齡齣齦齧齪齬齲齶齷龍龎龐龔龕龜鿓"
_T2S_SIMP = "丢并干乱亘亚伫布占并来仑侣局俣系伣侠伡私伥俩俫仓个们幸伦伟侧侦伪杰伧伞备家佣偬传伛债伤倾偻仅佥侨仆伪侥偾雇价仪俊侬亿侩俭傤傧俦侪尽偿优储俪傩傥俨凶兑儿兖内两册胄幂净冻凛凯别删刭则克刹刬刚剥剐剀创铲划剧刘刽刿剑剂劲动务勋胜劳势勚劢勋励劝匀匦汇匮区协恤却即厍厕历厌厉厣参叁丛咤吴呐吕呙员呗念问启哑启唡唤丧吃乔单哟呛啬唝吗呜唢哔叹喽啯呕啧尝唛哗唠啸叽哓呒啴恶嘘咝哒哝哕嗳哙喷吨当咛吓哜尝噜啮咽呖咙向亸喾严嘤啭嗫嚣冁呓啰苏嘱囱囵国围园圆图团坝垭采执坚垩垴埚尧报场块茔垲埘涂冢坞埙尘堑垫坠堕坛坟垯墙垦坛垱埙压垒圹垆坛坏垄垅坜坝塆壮壶壸寿够梦伙夹奂奥奁夺奖奋姹妆姗奸娱娄妇娅娲妫媪妈袅妪妩娴娴婳妫媭娆婵娇嫱嫒嬷嫔婴婶娘娈孙学孪宫采寝实宁审写宽宠宝将专寻对导尴届尸屃屉屡层屦属冈峰岘岛峡崃昆岗仑峥岽岚岁嵝崭岖嵚崂峤峣峄峃崄嵘岭屿岳岿峦巅岩巯卺帅师帐带帧帏帼帻帜币帮帱干几库厕厢厩厦庼荫厨厮庙厂庑废广廪庐厅弑吊弪张强别弹弥弯录汇彟彦雕彨佛后径从徕复征彻恒耻悦悮怅闷凄恶恼恽恻爱惬悫怆恺忾栗态愠惨惭恸惯悫怄怂虑悭庆戚欲忧惫怜凭愦慭惮愤悯怃宪忆恳应怿懔蒙怼懑恹惩懒怀悬忏惧慑恋戆戋戗戬战戯戏户抛捝挲挟舍扪挨卷扫抡挜挣挂采拣扬换挥搄损摇捣揾抢掴掼搂挚抠抟折掺捞挦撑挠挢掸拨抚扑揿挞挝捡拥掳择击挡担据挤捣拟摈拧搁掷扩撷摆擞撸扰摅撵拢拦撄搀撺携摄攒挛摊搅揽教敚败叙敌数敛毙敩斓斩断于旗既升时晋昼晕晖旸畅暂晔历昙晓向暧旷昽晒书会胧术东拐栅拐查杆栀枧条枭棁弃棋枨枣栋栈栖梾桠杨枫桢业极矩干杩荣榅桤构枪杠梿椠椁椮桨椢椝桩乐枞梁楼标枢样榝桪朴树桦椫桡桥机椭横檩柽档桧槚检樯梼台槟柠槛柜橹榈栉椟橼栎橱槠栌枥橥榇蘖栊榉樱栏榉权椤栾榄棂钦叹欧欤欢岁历归殁残殒殇殚僵殓殡歼杀壳壳毁殴毵牦毡氇气氢氩氲泛泛污决没冲况溯泄汹浃泾涚凉凄泪渌净凌沦渊涞浅涣减沨涡测浑凑浈涌汤沩准沟温浉涢湿沧灭涤荥汇沪滞渗卤浒浐滚满渔溇沤汉涟渍涨溆渐浆颍泼洁沩潜润浔溃滗涠涩浇涝沄涧渑泽滪泶浍淀浊浓湿泞溁蒙浕济涛滥潍滨溅泺滤澛滢渎泻沈浏濒泸沥潇潆潴泷濑弥潋澜沣滠洒漓滩灏湾滦滟滟灾为乌烃无炼炜烟茕焕烦炀煴荧炝热颎炽烨灯炖烧烫焖营灿毁烛烩熏烬焘烁炉烂争为爷尔床墙牍牵荦牦犊牺状狭狈狰犹狲犸呆狱狮奖独狯猃狝狞获猎犷兽獭献猕猡现雕珐珲玮玚琐瑶莹玛玱琏琎玑瑷珰环玙瑸玺璇琼珑璎瓒瓯瓮产产苏宁亩毕画异画当畴叠痉酸疴痖疯疡痪瘗疮疟瘆疭瘘瘘疗痨痫瘅愈疠瘪痴痒疖症疬癞癣瘿瘾痈瘫癫发皂皑疱皲皱杯盗盏尽监盘卢荡真眦众困睁睐眍瞒瞆睑蒙眬瞩矫朱硁硖砗砚埼硕砀砜确码硙砖硵碜碛矶硗硚硷础碍矿砺砾矾砻秘禄祸祯祎祃御禅礼祢祷秃籼税秆棱禀种称谷稣积颖秾穑秽稳获穞窝洼穷窑窎窭窥窜窍窦灶窃竖竞笔笋笕个笺筝节范筑箧筼筿笃筛筚箦篓蓑箪简篑箫筜签帘篮筹箓篯箨籁笼签笾簖篱箩吁粤粽糁粪粮团粝籴粜纟纠纪纣约红纡纥纨纫纹纳纽纾纯纰纼纱纮纸级纷纭纴纺扎细绂绁绅纻绍绀绋绐绌终弦组绊绗结绝绦绔绞络绚给绒绖统丝绛绝绢绑绡绠绨绣绤绥捆经综缍绿绸绻线绶维绹绾纲网绷缀彩纶绺绮绽绰绫绵绲缁紧绯绿绪绬绱缃缄缂线缉缎缔缗缘缌编缓缅纬缑缈练缏缇致缊萦缙缢缒绉缣缊缞缚缜缟缛县绦缝缡缩纵缧纤缦絷缕缥总绩绷缫缪缯织缮缭绕绣缋绳绘系茧缰缳缲缴绎继缤缱颣缬纩续累缠缨才纤缵缆钵坛罂坛罚骂罢罗罴羁芈群羟羡义膻习玩翚翘翙耧耢圣闻联聪声耸聩聂职聍听聋肃胁脉胫唇修脱胀肾胨脶脑肿脚肠腽腘肤胶腻胆脍脓脸脐膑腊胪脏脔臜卧临台与兴举旧馆舱舣舰舻艰艳刍苎兹荆庄茎荚苋华庵烟苌莱万荝莴叶荭荮苇药荤搜莼莳蒀莅苍荪席盖莲苁莼荜卜参蒌蒋葱茑荫荨蒇荞荬芸莸荛蒉荡芜萧蓣蕰荟蓟芗姜蔷荙莶荐萨苧苔荠蓝荩艺药薮蕴苈蔼蔺萚蕲芦苏蕴苹藓蔹茏兰蓠萝蔂处虚虏号亏虬蛱蜕蚬蚀猬虾虱蜗蛳蚂萤蝼螀蛰蝈螨虮蝉蛲虫蛏蚁蚃蝇虿蝎蛴蝾蚝蜡蛎蟏蛊蚕蛮众蔑术同胡卫冲衮袅里补装里制复裈袆裤裢褛亵裥裥袯袄裣裆褴袜摆衬袭襕核见觃规觅视觇觋觍觎亲觊觏觐觑觉览觌观觞觯触讠订讣计讯讧讨讦讱训讪讫托记讹讶讼诀讷讻访设许诉诃诊注证诂诋讵诈诒诏评诐诇诎诅词咏诩询诣试诗诧诟诡诠诘话该详诜诙诖诔诛诓夸志认诳诶诞诱诮语诚诫诬误诰诵诲说说谁课谇诽谊訚调谄谆谈诿请诤诹诼谅论谂谀谍谞谝谥诨谔谛谐谏谕咨讳谙谌讽诸谚谖诺谋谒谓誊诌谎谜谧谑谡谤谦谥讲谢谣谣谟谪谬谫讴谨谩哗证谲讥谮识谯谭谱噪谵毁译议谴护诪誉谫读谉变詟雠谗让谰谶赞谠谳岂竖丰艳猪豮猫贝贞贠负财贡贫货贩贪贯责贮贳赀贰贵贬买贷贶费贴贻贸贺贲赂赁贿赅资贾贼赈赊宾赇赒赉赐赏赔赓贤卖贱赋赕质赍账赌赖赗赚赙购赛赜贽赘赟赠赞赝赡赢赆赃赑赎赝赣赃赪赶赵趋趱迹践逾踊跄跸迹跖蹒踪跷跶趸踌跻跃踯跞踬蹰跹蹑蹿躜躏躯车轧轨军轪轩轫轭软轷轸轱轴轵轺轲轶轼较辂辁辀载轾辄挽辅轻辆辎辉辋辍辊辇辈轮辌辑辏输辐辒辗舆辒毂辖辕辘转辙轿辚轰辔轹轳办辞辫辩农回迳这连周进游运过达违遥逊递远溯适迟迁选遗辽迈还迩边逻逦郏邮郓乡邹邬郧邓郑邻郸邺郐邝酂郦腌酝丑酝蒏糖医酱酦酿衅酾酽释厘钅钆钇钌钊钉钋针钓钐扣钏钒钗钍钕钎钯钫钘钭钥钚钠钝钩钤钣钑钞钮钧钟钙钬钛钪铌铈钶铃钴钹铍钰钸铀钿钾巨钻铊铉铇铋铂钷钳铆铅钺钵钩钲钼钽锫铏铰铒铬铪银铳铜铚铣铨铢铭铫铦衔铑铷铱铟铵铥铕铯铐铞锐销锈锑锉铝锒锌钡铤铗锋铻锊锓铘锄锃锔锇铓铺锐铖锆锂铽锍锯钢锞录锖锫锩铔锥锕锟锤锱铮锛锬锭锜钱锦锚锠锡锢错录锰表铼镎锝锨锪钫钔锴锳炼锅镀锷铡钖锻锽锸锲锘锹锾键锶锗针钟镁锿镅镑镰镕锁镉锤镈镃钨蓥镏铠铩锼镐镇镇镒镋镍镓鿔镌镎镞旋链镆镙镠镝铿锵镗镘镛铲镜镖镂錾镚铧镤镪锈铙铴镣铹镦镡钟镫镢镨锎锏镄镌镰镯镭铁镮铎铛镱铸镬镔鉴鉴镲锧镴铄镳镥镧钥镵镶镊镩锣钻銮凿镢镋长门闩闪闫闬闭开闶闳闰闲闲间闵闸阂阁合阀闺闽阃阆闾阅阅阊阉阎阏阍阈阌阒板暗闱阔阕阑阇阗阘闿阖阙闯关阚阓阐辟阛闼陉陕升阵阴陈陆阳陧队阶陨际随险陦隐陇隶只隽虽双雏杂鸡离难云电沾霡雾霁雳霭叇灵叆靓静靔腼靥巩绱秋鞒缰鞑千鞯韦韧韨韩韪韬鞲韫韵响页顶顷项顺顸须顼颂颀颃预顽颁顿颇领颌颉颐颏头颒颊颋颕颔颈颓频颓颗题额颚颜颙颛颜愿颡颠类颟颢顾颤颥显颦颅颞颧风飐飑飒台刮飓飔飏飖飕飗飘飙飚飞饣饥饤饦饨饪饫饬饭飧饮饴饲饱饰饳饺饸饼糍饷养饵饹饻饽馁饿馂饾余肴馄馃饯馅馆糊糇饧喂馉馇馎饩馏馊馌馍馒馐馑馓馈馔饥饶飨餍馋馕马驭冯驮驰驯驲驳驻驽驹驵驾骀驸驶驼驷骂骈骇骃骆骎骏骋骍骓骔骒骑骐骛骗骙骞骘骝腾驺骚骟骡蓦骜骖骠骢驱骅骕骁骣骄验惊驿骤驴骧骥骦骊骉肮髅脏体髌髋发松胡须鬓斗闹哄阋阄郁鬶魉魇鱼鱽鱾鲀鲁鲂鱿鲄鲅鲆鲌鲉鲏鲇鲐鲍鲋鲊鲒鲘鲞鲕鲖鲔鲛鲑鲜鲓鲪鲝鲧鲠鲩鲤鲨鲬鲻鲯鲭鲞鲷鲴鲱鲵鲲鲳鲸鲮鲰鲶鲺鳀鲫鳊鳈鲗鳂鲽鳇鳅鲾鳄鳆鳃鳁鳒鳑鳋鲥鳏鳎鳐鳍鳁鲢鳌鳓鳘鲦鲣鲹鳗鳛鳔鳉鳙鳕鳖鳟鳝鳜鳞鲟鲼鲎鲙鳣鳡鳢鲿鲚鳠鳄鲈鲡鸟凫鸠凫鸤凤鸣鸢鸩鸨鸦鸰鸵鸳鸲鸮鸱鸪鸯鸭鸸鸹鸻鸿鸽鸺鸼鹀鹃鹆鹁鹈鹅鹄鹉鹌鹏鹐鹎雕鹊鹓鹍鸫鹑鹒鹋鹙鹕鹗鹖鹛鹜鸧莺鹟鹤鹠鹡鹘鹣鹚鹚鹢鹞鸡鹝鹧鹥鸥鸷鹨鸶鹪鹔鹩鹫鹇鹇鹬鹰鹭鸴鹯鹱鹲鸬鹴鹦鹳鹂鸾卤咸鹾碱盐丽麦麸面面曲曲面么么黄黉点党黪霉黡黩黾鼋鼌鼍冬鼹齐斋赍齑齿龀龁龂龅龇龃龆龄出龈啮龊龉龋腭龌龙厐庞龚龛龟鿒"
_T2S = str.maketrans(_T2S_TRAD, _T2S_SIMP)


def _simp(t):
    return str(t or "").translate(_T2S)


def _sn(t):
    """比较用：转简体 + _norm（去括号里的、去标点空格、小写）"""
    return _norm(_simp(t))


def title_variants(title):
    """点的歌名的几种写法（都是 _sn 过的）：整个歌名、括号里的另一个名字（「茉莉花（Jasmine Flower）」→ 茉莉花 / jasmineflower）、
    「/」「｜」隔开的几个名字。空的去掉、去重"""
    t = _simp(title)
    out = [_norm(t)]
    for seg in re.findall(r"[（(【\[]\s*([^（）()【】\[\]]{2,40}?)\s*[)）】\]]", t):
        out.append(_norm(seg))
    for part in re.split(r"[/／|｜]", re.sub(r"[（(【\[].*?[)）】\]]", "", t)):
        out.append(_norm(part))
    seen, res = set(), []
    for v in out:
        if v and v not in seen:
            seen.add(v)
            res.append(v)
    return res


def _quoted(vtitle):
    """标题里《》「」『』括起来的几段（_sn 过的）"""
    return [_sn(x) for x in re.findall(r"[《「『]([^《》「」『』]{1,60})[》」』]", _simp(vtitle))]


def check_title(vtitle, up, title, artist="", raw_req=""):
    """B站 视频标题核对 → (ok, 分数, 理由)。理由是给日志看的中文，通过时写「为什么选它」，没通过时写「为什么不要」
    ① 标题里要有歌名（繁简 / 空格 / 大小写 / 括号里的另一个名字都认）；② 不是 混剪 MV 字幕组 动画 合集 翻唱 伴奏 教学 这类
    （点歌的人自己写了这个词的除外）；③ 点了歌手的：标题或 UP 主里要有歌手；④ 歌名不能只是顺带提到：标题里去掉歌名、歌手、
    括号装饰、优先词以后剩的字不能多于 MAX_LEFTOVER，歌名在《》里的除外；⑤ 带 原唱 / 官方 / live / 歌词版 的加分"""
    vt = str(vtitle or "")
    vs = _sn(vt)
    variants = title_variants(title)
    if not variants:
        return False, 0, "没说歌名"
    hit = next((v for v in variants if v in vs), None)
    if not hit:
        return False, 0, f"标题里没有歌名「{str(title)[:20]}」"
    req_bad = bool(NOT_ORIG.search(raw_req or "") or BAD_VIDEO.search(raw_req or ""))
    m = BAD_VIDEO.search(vt) or NOT_ORIG.search(vt)
    if m and not req_bad:
        return False, 0, f"标题带「{m.group(0)}」不是原唱版"
    if artist:
        an = _sn(artist)
        if not (an and (an in vs or an in _sn(up) or (_sn(up) and _sn(up) in an))):
            return False, 0, f"标题和 UP 主里都没有歌手「{str(artist)[:12]}」"
    quoted = _quoted(vt)
    in_quote = any(hit == q or hit in q for q in quoted)
    rest = _sn(re.sub(r"[【\[（(][^】\]）)]*[】\]）)]", " ", vt))      # 去掉【】[]（）装饰
    rest = rest.replace(hit, "", 1)
    if artist:
        rest = rest.replace(_sn(artist), "")
    for w in PREFER_STRONG.findall(_simp(vt)) + PREFER.findall(_simp(vt)):
        rest = rest.replace(_sn(w), "")
    rest = re.sub(r"[《》「」『』]", "", rest)
    ascii_left = len(re.sub(r"[^a-z0-9]", "", rest))      # 英文 / 数字不算多（常见的英文译名、编号）；汉字 / 外文字母才是「别的内容」
    other_left = len(rest) - ascii_left
    if not in_quote and (other_left > MAX_LEFTOVER or ascii_left > MAX_LEFTOVER_ASCII):
        return False, 0, f"歌名只是顺带提到（标题里还有 {other_left} 个汉字 / 外文字母、{ascii_left} 个英文字母别的：{rest[:16]}…）"
    strong = PREFER_STRONG.findall(vt)
    weak = PREFER.findall(vt)
    sc = (6 if strong else 0) + (1 if weak else 0) + (3 if in_quote else 0) + (5 if artist else 0)
    why = f"标题含歌名「{hit}」" + ("、在《》里" if in_quote else "") + ("、歌手对得上" if artist else "") \
        + (f"、带「{strong[0]}」" if strong else "") + (f"、带「{weak[0]}」" if weak else "")
    return True, sc, why


def _recheck_cached(hit, title, artist, raw_req, studio=False, bad=()):
    """记过的旧结果（data/song_online.json）按现在的规矩再核对一遍 → (ok, 理由)。只有 B站 记了视频标题的才能核对；网易云的、以前没记标题的照旧用
    11-01 v1.15.18：有人说过不是这首的 → 不用；要换声时 记的是现场版、当时又不是只找得到现场版 → 不用（以前记过的一直照用
    v1.15.14 / v1.15.15 加的「现场版往后排」对它们不起作用 —— 点 See You Again 一直是小饭桌现场版）"""
    if str(hit.get("id") or "") in {str(b) for b in bad}:
        return False, "有人说过这个版本不是他要的"
    vid = str(hit.get("video") or "")
    src = hit.get("source")
    # 11-01 v1.16.0：歌曲平台（网易云 / QQ / 酷狗）没有视频标题 —— 看歌名里有没有 Live / 现场
    live_txt = vid or (str(hit.get("title") or "") if src in ("网易云", "QQ音乐", "酷狗") else "")
    if studio and live_txt and LIVE.search(live_txt) and not hit.get("live_only"):
        return False, "是现场版（要换声：观众声和混响分不干净 换出来糊）"
    if src not in ("B站", "YouTube") or not vid:       # YouTube 记的视频标题也按 check_title 再核对（11-01 v1.16.0）
        return True, ""
    ok, _, why = check_title(vid, hit.get("up") or "", title, artist, raw_req)
    return ok, why


def _parse_duration(text):
    """ffprobe 给的纯秒数（「201.34」）或 ffmpeg -i 的「Duration: 00:03:21.45」→ 秒；读不出 None"""
    t = str(text or "")
    m = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", t)
    if m:
        return int(m.group(1)) * 3600 + int(m.group(2)) * 60 + float(m.group(3))
    m = re.match(r"^\s*(\d+(?:\.\d+)?)\s*$", t)
    return float(m.group(1)) if m else None


def real_seconds(path):
    """下载下来的音频真实多长（秒）。先 ffprobe 再 ffmpeg -i；都不行 None（调用的地方当「量不出来」处理）"""
    ff = _ff()
    cands = []
    probe = shutil.which("ffprobe")
    if not probe and ff:
        alt = Path(ff).with_name("ffprobe" + Path(ff).suffix)
        probe = str(alt) if alt.exists() else None
    if probe:
        cands.append(([probe, "-v", "error", "-show_entries", "format=duration", "-of", "default=nk=1:nw=1", str(path)], "out"))
    if ff:
        cands.append(([ff, "-hide_banner", "-i", str(path)], "err"))
    for cmd, where in cands:
        try:
            r = subprocess.run(cmd, capture_output=True, timeout=60, creationflags=NO_WINDOW)
        except Exception:
            continue
        txt = (r.stdout if where == "out" else r.stderr).decode("utf-8", "replace")
        d = _parse_duration(txt)
        if d is not None and d > 0:
            return d
    return None


# ───────────── 网络请求（11-01 v1.16.0：全集中在这几个函数里 —— 测试换掉它们就不联网）─────────────
class _Resp:
    """_http 的结果：跟 httpx 的 Response 用法一样（status_code / headers / content / url / text / json()）"""

    def __init__(self, status_code=0, headers=None, content=b"", url=""):
        self.status_code = int(status_code or 0)
        self.headers = {str(k).lower(): str(v) for k, v in (headers or {}).items()}
        self.content = content if isinstance(content, (bytes, bytearray)) else str(content or "").encode("utf-8")
        self.url = str(url or "")

    @property
    def text(self):
        return bytes(self.content).decode("utf-8", "replace")

    def json(self):
        """宽容：去掉 BOM、JSONP 的「callback( … )」；读不出 None（不抛）"""
        t = self.text.strip().lstrip("﻿")
        m = re.match(r"^[\w$.]+\s*\((.*)\)\s*;?\s*$", t, re.S)
        if m:
            t = m.group(1)
        try:
            return json.loads(t)
        except ValueError:
            return None


def _cookie_str(ck):
    """登录 cookie → Cookie 头（值里有分号 / 换行的不要）。这串字不进日志"""
    return "; ".join(f"{k}={v}" for k, v in (ck or {}).items()
                     if k and v is not None and str(v) != "" and not re.search(r"[;\r\n]", f"{k}{v}"))


def _http(method, url, *, params=None, data=None, json_body=None, headers=None, cookies=None, timeout=15, follow=True):
    """发一个请求 → _Resp；连不上 / 超时照样抛（调用的地方换成中文原因）"""
    h = {"User-Agent": UA}
    h.update(headers or {})
    if cookies:
        ck = _cookie_str(cookies)
        if ck:
            h["Cookie"] = (h["Cookie"] + "; " + ck) if h.get("Cookie") else ck
    with httpx.Client(timeout=timeout, follow_redirects=follow) as c:
        r = c.request(method, url, params=params, data=data, json=json_body, headers=h)
    return _Resp(r.status_code, dict(r.headers), r.content, str(r.url))


def _download(url, *, headers=None, cookies=None, max_bytes=MAX_BYTES * 2, timeout=60):
    """下整个音频 → (bytes, "") / (None, 中文原因)"""
    h = {"User-Agent": UA, "Accept": "*/*"}
    h.update(headers or {})
    if cookies:
        h["Cookie"] = _cookie_str(cookies)
    try:
        buf = bytearray()
        with httpx.Client(timeout=timeout, follow_redirects=True) as c:
            with c.stream("GET", url, headers=h) as r:
                if r.status_code not in (200, 206):
                    return None, f"音频地址打不开（HTTP {r.status_code}）"
                for ch in r.iter_bytes():
                    buf += ch
                    if len(buf) > max_bytes:
                        return None, f"音频太大了（超过 {max_bytes // 1024 // 1024}MB）"
    except Exception as e:
        return None, f"音频下不下来（{repr(e)[:60]}）"
    if len(buf) < 10000:
        return None, "下下来是空的"
    return bytes(buf), ""


def _ext(url, default=".mp3"):
    m = re.search(r"\.(mp3|m4a|flac|aac|ogg|opus|webm|wav)(?:[?#]|$)", str(url or ""), re.I)
    return "." + m.group(1).lower() if m else default


# ───────────── 主人登录的账号（musiclogin.py 管扫码、存 cookie；这里只读）─────────────
def _login_cookies(platform):
    """musiclogin.cookies(platform) → dict；import 不到 / 出错 / 没登录 = {}（11-01 v1.16.0）"""
    try:
        import musiclogin
        ck = musiclogin.cookies(platform)
    except Exception:
        return {}
    return {str(k): str(v) for k, v in ck.items() if v is not None} if isinstance(ck, dict) else {}


def _qq_uin(ck):
    """QQ 登录的 uin（cookie 里是「o0123…」这种，去掉 o 和前面的 0）；微信登录的是 wxuin"""
    for k in ("uin", "wxuin", "p_uin", "qqmusic_uin"):
        v = re.sub(r"\D", "", str((ck or {}).get(k) or "")).lstrip("0")
        if v:
            return v
    return ""


def _qq_key(ck):
    for k in ("qqmusic_key", "qm_keyst", "music_key"):
        if (ck or {}).get(k):
            return str(ck[k])
    return ""


def _kg_auth(ck):
    """酷狗网页登录的 cookie：KuGoo=KugooID=…&t=token&… 拆出 userid / token；mid / dfid 没有就给个默认"""
    ck = ck or {}
    kg = urllib.parse.parse_qs(str(ck.get("KuGoo") or ck.get("kugoo") or ""))
    userid = str(ck.get("userid") or ck.get("KugooID") or (kg.get("KugooID") or [""])[0] or "")
    token = str(ck.get("token") or ck.get("t") or (kg.get("t") or [""])[0] or "")
    mid = str(ck.get("kg_mid") or ck.get("mid") or hashlib.md5(UA.encode()).hexdigest())
    dfid = str(ck.get("kg_dfid") or ck.get("dfid") or "-")
    return {"userid": userid, "token": token, "mid": mid, "dfid": dfid}


def _logged(src, ck=None):
    ck = _login_cookies(src) if ck is None else ck
    if src == "qq":
        return bool(_qq_uin(ck) and _qq_key(ck))
    if src == "kugou":
        a = _kg_auth(ck)
        return bool(a["userid"] and a["token"])
    if src == "netease":
        return bool(ck.get("MUSIC_U"))
    return False


# ───────────── 网易云 ─────────────
def _ne_headers(ck=None):
    """网易云的请求头；主人登录了带上他的 cookie（MUSIC_U 等，11-01 v1.16.0）"""
    ck = _login_cookies("netease") if ck is None else ck
    h = dict(NE_HEADERS)
    extra = _cookie_str({k: v for k, v in ck.items() if k not in ("os", "appver")})
    if extra and ck.get("MUSIC_U"):
        h["Cookie"] = NE_HEADERS["Cookie"] + "; " + extra
    return h


def ne_search(title, artist, client=None, log=print):
    """（client 参数留着兼容 以前传 httpx.Client 现在不用了：请求都走 _http 11-01 v1.16.0）"""
    if ne_abroad():
        return []
    q = f"{title} {artist}".strip()
    try:
        r = _http("GET", "https://music.163.com/api/search/get/web",
                  params={"s": q, "type": 1, "offset": 0, "limit": 15}, headers=_ne_headers())
        j = r.json()
    except Exception as e:
        log(f"[唱歌] 网易云搜不了（{repr(e)[:80]}）")
        return []
    if not isinstance(j, dict):
        log(f"[唱歌] 网易云回的不是正常的搜索结果（{type(j).__name__}）")
        return []
    res = j.get("result")
    if j.get("abroad") or isinstance(res, str):
        now = time.time()
        _NE["abroad_until"] = now + ABROAD_HOLD
        if now - _NE["told"] > ABROAD_HOLD:
            _NE["told"] = now
            log("[唱歌] 网易云把这台电脑当成海外 IP：搜索结果是加密的（abroad）用不了 → 现找先走别的来源（6 小时后再试网易云）")
        return []
    songs = (res.get("songs") if isinstance(res, dict) else None) or []
    out = []
    for s in songs:
        if not isinstance(s, dict):
            continue
        names = [a.get("name") or "" for a in s.get("artists") or s.get("ar") or [] if isinstance(a, dict)]
        if not _ok_name(s.get("name"), title, f"{title} {artist}"):
            continue
        if not _artist_ok(names, artist):
            continue
        out.append({"id": s.get("id"), "name": s.get("name"), "artists": names,
                    "dur": float(s.get("duration") or s.get("dt") or 0) / 1000.0, "fee": s.get("fee")})
    logged = _logged("netease")
    out.sort(key=lambda x: (bool(LIVE.search(str(x["name"]))),          # 录音室原版排前面
                            x["fee"] not in (0, 8, None) and not logged))   # 再是免费能播的（登录了不看这个 11-01 v1.16.0）
    return out


def _ne_audio_ok(r):
    if r.status_code != 200 or "404" in str(r.url) or len(r.content) < 50_000:
        return False
    return "audio" in r.headers.get("content-type", "") or r.content[:3] in (b"ID3",) \
        or r.content[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2") or r.content[:4] == b"fLaC"


def ne_audio(sid, client=None):
    """网易云音频 bytes；放不了 None（原因在 _NE["why"]）
    11-01 v1.16.0 主人登录了：先用 /api/song/enhance/player/url 拿他账号能放的地址（freeTrialInfo 不是空 = 只给试听 → 不要）；
    没登录 / 拿不到 → 官方外链（免费歌给 mp3；VIP / 只给大陆播的会跳到 404）"""
    _NE["why"] = ""
    ck = _login_cookies("netease")
    if ck.get("MUSIC_U"):
        try:
            j = _http("GET", "https://music.163.com/api/song/enhance/player/url",
                      params={"ids": f"[{int(sid)}]", "br": 320000}, headers=_ne_headers(ck)).json() or {}
            d = (j.get("data") or [{}])[0] if isinstance(j, dict) else {}
            d = d if isinstance(d, dict) else {}
            if d.get("url") and not d.get("freeTrialInfo"):
                a, w = _download(d["url"], headers={"Referer": "https://music.163.com/"})
                if a:
                    return a[:MAX_BYTES]
                _NE["why"] = f"登录了拿到地址但{w}"
            elif d.get("freeTrialInfo"):
                _NE["why"] = "登录的账号也只能听试听片段（要 VIP / 单曲付费）"
            else:
                _NE["why"] = f"登录的账号也拿不到播放地址（没版权 / 只给大陆播 代码 {d.get('code') or j.get('code')}）"
        except Exception as e:
            _NE["why"] = f"用登录的账号取地址出错（{repr(e)[:50]}）"
    try:
        r = _http("GET", f"https://music.163.com/song/media/outer/url?id={int(sid)}.mp3", headers=NE_HEADERS, follow=True)
    except Exception as e:
        _NE["why"] = _NE["why"] or f"外链打不开（{repr(e)[:50]}）"
        return None
    if not _ne_audio_ok(r):
        _NE["why"] = _NE["why"] or "放不了（VIP 或者只给大陆播）"
        return None
    return r.content[:MAX_BYTES]


def ne_lyric(sid, client=None):
    try:
        j = _http("GET", "https://music.163.com/api/song/lyric",
                  params={"id": int(sid), "lv": 1, "kv": 1, "tv": -1}, headers=_ne_headers()).json()
        return ((j or {}).get("lrc") or {}).get("lyric") or ""
    except Exception:
        return ""


def ne_detail(sid):
    """网易云歌曲 id → {"id", "name", "artists", "dur"}（链接点歌用）；读不到 None"""
    try:
        j = _http("GET", "https://music.163.com/api/song/detail/",
                  params={"id": int(sid), "ids": f"[{int(sid)}]"}, headers=_ne_headers()).json() or {}
    except Exception:
        return None
    s = ((j.get("songs") if isinstance(j, dict) else None) or [None])[0]
    if not isinstance(s, dict) or not s.get("name"):
        return None
    names = [a.get("name") or "" for a in s.get("artists") or s.get("ar") or [] if isinstance(a, dict)]
    return {"id": s.get("id") or int(sid), "name": s.get("name"), "artists": names,
            "dur": float(s.get("duration") or s.get("dt") or 0) / 1000.0, "fee": s.get("fee")}


# ───────────── QQ 音乐（11-01 v1.16.0，凭知识写的：u.y.qq.com/cgi-bin/musicu.fcg）─────────────
QQ_API = "https://u.y.qq.com/cgi-bin/musicu.fcg"
QQ_HEADERS = {"Referer": "https://y.qq.com/", "Origin": "https://y.qq.com"}
QQ_GUID = str(random.randint(1_000_000_000, 9_999_999_999))
QQ_FILES = (("C400", ".m4a"), ("M500", ".mp3"), ("M800", ".mp3"))   # 先要 AAC 96k（免费歌一般都有），再 128k / 320k mp3


def _g_tk(key):
    h = 5381
    for ch in str(key or ""):
        h += (h << 5) + ord(ch)
    return h & 0x7fffffff


def _qq_comm(ck):
    uin, key = _qq_uin(ck), _qq_key(ck)
    c = {"ct": 24, "cv": 0, "format": "json", "uin": int(uin) if uin else 0, "authst": key}
    if ck.get("tmeLoginType"):
        c["tmeLoginType"] = ck["tmeLoginType"]
    return c


def _qq_call(req, ck=None):
    """musicu.fcg 一次请求（req = {"req_0": {module, method, param}}）→ 回的 dict；连不上 / 不是 JSON 抛 RuntimeError（中文）"""
    ck = _login_cookies("qq") if ck is None else ck
    body = dict(req, comm=_qq_comm(ck))
    try:
        r = _http("POST", QQ_API, params={"g_tk": _g_tk(_qq_key(ck))}, json_body=body, headers=QQ_HEADERS, cookies=ck)
    except Exception as e:
        raise RuntimeError(f"连不上 QQ 音乐（{repr(e)[:60]}）")
    j = r.json()
    if not isinstance(j, dict):
        raise RuntimeError(f"QQ 音乐回的不是正常结果（HTTP {r.status_code}）")
    return j


def _qq_song(it):
    """QQ 音乐的一首（搜索 / 详情的格式都认，字段缺了给空）→ {"id"(songmid), "name", "artists", "dur", "vip", "x"}"""
    if not isinstance(it, dict):
        return None
    mid = it.get("mid") or it.get("songmid") or ""
    name = _strip_html(it.get("name") or it.get("songname") or it.get("title") or "")
    if not mid or not name:
        return None
    singers = it.get("singer") or it.get("singers") or []
    artists = [str(a.get("name") or a.get("title") or "") for a in singers if isinstance(a, dict)] if isinstance(singers, list) else []
    f = it.get("file") if isinstance(it.get("file"), dict) else {}
    pay = it.get("pay") if isinstance(it.get("pay"), dict) else {}
    return {"id": str(mid), "name": name, "artists": [a for a in artists if a],
            "dur": float(it.get("interval") or 0), "vip": bool(pay.get("pay_play") or pay.get("payplay")),
            "x": {"media_mid": f.get("media_mid") or it.get("strMediaMid") or it.get("media_mid") or str(mid),
                  "songid": it.get("id") or it.get("songid")}}


def qq_search_raw(q, ck=None):
    """QQ 音乐搜歌 → 每首 _qq_song 过的（没筛）。先 musicu 的 DoSearchForQQMusicDesktop，不行再 c.y.qq.com 的老搜索"""
    why = ""
    try:
        j = _qq_call({"req_0": {"module": "music.search.SearchCgiService", "method": "DoSearchForQQMusicDesktop",
                                "param": {"query": q, "search_type": 0, "num_per_page": 20, "page_num": 1, "grp": 1}}}, ck)
        d = (j.get("req_0") or {}).get("data") or {}
        lst = ((d.get("body") or {}).get("song") or {}).get("list") or []
        out = [s for s in map(_qq_song, lst) if s]
        if out or (j.get("req_0") or {}).get("code") == 0:
            return out
        why = f"代码 {(j.get('req_0') or {}).get('code')}"
    except RuntimeError as e:
        why = str(e)
    try:
        r = _http("GET", "https://c.y.qq.com/soso/fcgi-bin/client_search_cp",
                  params={"w": q, "format": "json", "p": 1, "n": 20, "cr": 1, "new_json": 1, "t": 0},
                  headers=QQ_HEADERS, cookies=ck if ck is not None else _login_cookies("qq"))
        j = r.json() or {}
        lst = (((j.get("data") or {}).get("song") or {}).get("list") or []) if isinstance(j, dict) else []
        return [s for s in map(_qq_song, lst) if s]
    except Exception as e:
        raise RuntimeError(f"搜不了（{why or repr(e)[:60]}）")


def qq_detail(mid="", songid=None, ck=None):
    """QQ 音乐一首歌的信息（链接点歌用）→ _qq_song 的格式；读不到 None"""
    param = {"song_mid": mid} if mid else {"song_id": int(songid or 0)}
    try:
        j = _qq_call({"req_0": {"module": "music.pf_song_detail_svr", "method": "get_song_detail_yqq", "param": param}}, ck)
    except RuntimeError:
        return None
    d = (j.get("req_0") or {}).get("data") or {}
    return _qq_song(d.get("track_info") or d.get("songinfo") or {})


def qq_audio(s, ck=None):
    """QQ 音乐一首 → (bytes, "", 扩展名) / (None, 中文原因, "")。vkey 服务给 purl（空 = 这个账号没权限 / 地区限制 / 登录过期）"""
    ck = _login_cookies("qq") if ck is None else ck
    if not _logged("qq", ck):
        return None, "QQ 音乐没登录", ""
    mid = s["id"]
    media = (s.get("x") or {}).get("media_mid") or mid
    why = ""
    for pre, ext in QQ_FILES:
        try:
            j = _qq_call({"req_0": {"module": "vkey.GetVkeyServer", "method": "CgiGetVkey",
                                    "param": {"guid": QQ_GUID, "songmid": [mid], "songtype": [0], "uin": _qq_uin(ck),
                                              "loginflag": 1, "platform": "20", "filename": [f"{pre}{media}{ext}"]}}}, ck)
        except RuntimeError as e:
            return None, str(e), ""
        d = (j.get("req_0") or {}).get("data") or {}
        info = (d.get("midurlinfo") or [{}])[0] if isinstance(d.get("midurlinfo"), list) else {}
        info = info if isinstance(info, dict) else {}
        purl = info.get("purl") or ""
        if not purl:
            code = info.get("result") or (j.get("req_0") or {}).get("code")
            why = ("要 VIP / 付费（主人的账号没这个权限）" if s.get("vip") else "拿不到播放地址（没权限 / 地区限制 / 登录过期）") \
                + (f" 代码 {code}" if code else "")
            continue
        sips = [x for x in (d.get("sip") or []) if isinstance(x, str) and x.startswith("http")] or ["https://isure.stream.qqmusic.qq.com/"]
        for base in sips[:2]:
            a, w = _download(base + purl, headers={"Referer": "https://y.qq.com/"})
            if a:
                return a, "", ext
            why = w
    return None, why or "拿不到播放地址", ""


def qq_lyric(mid):
    """QQ 音乐歌词（LRC 文字；有时还是 base64 / 带 &#58; 这种转义 都解开）；拿不到 ""（不要紧 退回听副歌）"""
    try:
        j = _http("GET", "https://c.y.qq.com/lyric/fcgi-bin/fcg_query_lyric_new.fcg",
                  params={"songmid": mid, "format": "json", "nobase64": 1, "g_tk": 5381, "platform": "yqq.json", "needNewCode": 0},
                  headers=QQ_HEADERS).json() or {}
    except Exception:
        return ""
    t = str((j.get("lyric") if isinstance(j, dict) else "") or "")
    if t and "[" not in t:
        try:
            import base64
            t = base64.b64decode(t).decode("utf-8", "replace")
        except Exception:
            return ""
    return html.unescape(t)


# ───────────── 酷狗（11-01 v1.16.0，凭知识写的）─────────────
KG_HEADERS = {"Referer": "https://www.kugou.com/"}
# 酷狗网页版 play/songinfo 的签名盐：酷狗网页 JS 里公开的固定常量（appid 1014 的），不是谁的账号密钥
KG_WEB_SALT = "NVPh5oo715z5DIWAeQlhMDsWXXQV4hwt"


def _kg_song(it):
    """酷狗的一首（mobilecdn 的 v3 搜索 / song_search_v2 / 详情的格式都认）→ {"id"(hash), "name", "artists", "dur", "vip", "x"}"""
    if not isinstance(it, dict):
        return None
    h = str(it.get("hash") or it.get("FileHash") or it.get("Hash") or "")
    fname = _strip_html(it.get("filename") or it.get("FileName") or it.get("fileName") or "")
    name = _strip_html(it.get("songname") or it.get("SongName") or it.get("songName") or it.get("audio_name") or "")
    singer = _strip_html(it.get("singername") or it.get("SingerName") or it.get("singerName") or it.get("author_name") or "")
    if (not name or not singer) and " - " in fname:
        a, b = fname.split(" - ", 1)
        singer, name = singer or a.strip(), name or b.strip()
    if not re.fullmatch(r"[0-9A-Fa-f]{32}", h) or not name:
        return None
    dur = it.get("duration") or it.get("Duration") or 0
    if not dur and it.get("timeLength"):
        dur = it["timeLength"]
    dur = float(dur or 0)
    if dur > 3600:                     # 有的给毫秒
        dur /= 1000.0
    return {"id": h.upper(), "name": name, "artists": [x.strip() for x in re.split(r"[、,&/]", singer) if x.strip()],
            "dur": dur, "vip": str(it.get("pay_type") or it.get("PayType") or "0") not in ("0", ""),
            "x": {"album_id": str(it.get("album_id") or it.get("AlbumID") or it.get("albumid") or ""),
                  "eid": str(it.get("EMixSongID") or it.get("encode_album_audio_id") or "")}}


def kg_search_raw(q, ck=None):
    why = ""
    for url, params, path in (
            ("https://mobilecdn.kugou.com/api/v3/search/song",
             {"format": "json", "keyword": q, "page": 1, "pagesize": 20, "showtype": 1}, ("data", "info")),
            ("https://songsearch.kugou.com/song_search_v2",
             {"keyword": q, "page": 1, "pagesize": 20, "platform": "WebFilter", "filter": 2}, ("data", "lists"))):
        try:
            j = _http("GET", url, params=params, headers=KG_HEADERS).json()
        except Exception as e:
            why = repr(e)[:60]
            continue
        lst = j if isinstance(j, dict) else {}
        for p in path:
            lst = lst.get(p) if isinstance(lst, dict) else None
        if isinstance(lst, list):
            out = [s for s in map(_kg_song, lst) if s]
            if out:
                return out
        why = why or f"回的格式认不出（{str(j)[:40]}）"
    if why:
        raise RuntimeError(f"搜不了（{why}）")
    return []


def _kg_sign(p):
    s = "".join(sorted(f"{k}={v}" for k, v in p.items()))
    return hashlib.md5((KG_WEB_SALT + s + KG_WEB_SALT).encode("utf-8")).hexdigest()


def _kg_play(s, ck):
    """酷狗取播放信息：先老的 yy/index.php?r=play/getdata，不行再签名的 play/songinfo → (data dict, 原因)"""
    a = _kg_auth(ck)
    x = s.get("x") or {}
    why = ""
    reqs = [("https://wwwapi.kugou.com/yy/index.php",
             {"r": "play/getdata", "hash": s["id"], "album_id": x.get("album_id") or "", "dfid": a["dfid"], "mid": a["mid"],
              "platid": 4, "_": int(time.time() * 1000)})]
    p = {"srcappid": "2919", "clientver": "20000", "clienttime": str(int(time.time() * 1000)), "mid": a["mid"], "uuid": a["mid"],
         "dfid": a["dfid"], "appid": "1014", "platid": "4", "hash": s["id"], "album_id": x.get("album_id") or "",
         "token": a["token"], "userid": a["userid"]}
    if x.get("eid"):
        p["encode_album_audio_id"] = x["eid"]
    p["signature"] = _kg_sign(p)
    reqs.append(("https://wwwapi.kugou.com/play/songinfo", p))
    for url, params in reqs:
        try:
            j = _http("GET", url, params=params, headers=KG_HEADERS, cookies=ck).json()
        except Exception as e:
            why = f"连不上（{repr(e)[:50]}）"
            continue
        if not isinstance(j, dict):
            why = "回的不是正常结果"
            continue
        d = j.get("data")
        d = d[0] if isinstance(d, list) and d else d
        if isinstance(d, dict) and (d.get("play_url") or d.get("play_backup_url") or d.get("is_free_part")):
            return d, ""
        why = f"拿不到播放地址（status {j.get('status')} err_code {j.get('err_code') or j.get('errcode')}）"
    return None, why


def kg_audio(s, ck=None):
    """酷狗一首 → (bytes, "", 扩展名) / (None, 原因, "")。只给 60 秒试听（is_free_part）的直接说清（下下来也过不了 80 秒闸门）"""
    ck = _login_cookies("kugou") if ck is None else ck
    if not _logged("kugou", ck):
        return None, "酷狗没登录", ""
    d, why = _kg_play(s, ck)
    if not d:
        return None, why, ""
    if d.get("lyrics"):
        s.setdefault("x", {})["lyric"] = str(d["lyrics"])
    if str(d.get("is_free_part") or "0") not in ("0", "", "False"):
        return None, "只给 60 秒试听（VIP 歌 / 主人的账号没这个权限）", ""
    urls = [u for u in (d.get("play_url"), d.get("play_backup_url")) if u]
    for u in urls:
        a, w = _download(u, headers=KG_HEADERS)
        if a:
            return a, "", _ext(u)
        why = w
    return None, why or "拿不到播放地址", ""


def kg_detail(h, ck=None):
    """酷狗 hash → 歌名歌手（链接点歌用）；读不到 None"""
    try:
        j = _http("GET", "https://m.kugou.com/app/i/getSongInfo.php", params={"cmd": "playInfo", "hash": h},
                  headers=KG_HEADERS, cookies=ck if ck is not None else _login_cookies("kugou")).json()
    except Exception:
        return None
    if not isinstance(j, dict):
        return None
    s = _kg_song(dict(j, hash=j.get("hash") or h))
    return s


# ───────────── YouTube（11-01 v1.16.0：yt-dlp 可选；现在下 YouTube 要一个 JS 运行时 Deno）─────────────
DENO_LOCAL = ROOT.parent / "tools" / "deno"     # 菜单「YouTube 点歌支持」winget 不行时把 Deno 直接下到这里（11-01 v1.16.1）


def deno_candidates():
    """Deno 可能在的地方（11-01 v1.16.1：winget 装的「便携包」只在 WinGet\\Packages\\DenoLand.Deno_*\\ 里、
    没建 Links 的快捷方式 机器人又是装之前开的 PATH 里也没有 → 以前找不到；菜单下到 C:\\QQBot\\tools\\deno 的也认）"""
    exe = "deno.exe" if os.name == "nt" else "deno"
    cands = [DENO_LOCAL / exe, Path.home() / ".deno" / "bin" / exe]
    la = os.environ.get("LOCALAPPDATA")
    if la:
        cands.append(Path(la) / "Microsoft" / "WinGet" / "Links" / exe)
        try:
            cands += sorted((Path(la) / "Microsoft" / "WinGet" / "Packages").glob(f"DenoLand.Deno*/{exe}"))
        except OSError:
            pass
    return cands


def _deno():
    p = shutil.which("deno")
    if p:
        return p
    for c in deno_candidates():
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None


def _yt_ready():
    """YouTube 能不能用 → (ok, 原因)"""
    try:
        import importlib.util
        have = importlib.util.find_spec("yt_dlp") is not None      # 只看装没装（import 一次要好几百毫秒）
    except Exception:
        have = False
    if not have:
        return False, "没装 yt-dlp（pip install yt-dlp）"
    if not _deno():
        return False, "没找到 Deno（现在 yt-dlp 下 YouTube 要一个 JS 运行时：装上 Deno 再试）"
    return True, ""


class _YtLog:
    def __init__(self):
        self.err = ""

    def debug(self, m):
        pass

    def info(self, m):
        pass

    def warning(self, m):
        pass

    def error(self, m):
        self.err = str(m)


def _yt_why(msg):
    """yt-dlp 的英文报错 → 人看得懂的中文原因"""
    m = str(msg or "")
    low = m.lower()
    if re.search(r"confirm your age|age[- ]restrict|inappropriate for some users", low):
        return "要登录确认年龄（不绕）"
    if "not a bot" in low or "sign in to confirm" in low:
        return "YouTube 要登录确认不是机器人（这台电脑的 IP 被拦了）"
    if "available in your country" in low or ("geo" in low and "restrict" in low):
        return "这个视频在这边放不了（地区限制）"
    if "private video" in low or "video unavailable" in low or "has been removed" in low:
        return "视频没了 / 私密"
    if re.search(r"js\s*runtime|javascript runtime|n challenge|n-sig|signature.*solv|solv.*challenge", low):
        return "缺 JS 运行时 / 解不开 YouTube 的校验（装 Deno、更新 yt-dlp）"
    if "max_filesize" in low or "larger than max" in low:
        return "音频太大了"
    if re.search(r"unable to download|urlopen error|timed out|connection|proxy|getaddrinfo|name resolution", low):
        mm = re.search(r"(urlopen error|ProxyError|timed out|getaddrinfo).{0,50}", m)
        return f"连不上 YouTube（{(mm.group(0) if mm else m)[:60]}）"
    return f"yt-dlp 出错（{m.replace('ERROR:', '').strip()[:80]}）"


def _yt_extract(target, download=False, flat=False):
    """yt-dlp 一次（搜索「ytsearchN:关键词」或一个视频）→ info dict；下载的多一个 "_path"。出错抛 RuntimeError（中文）"""
    try:
        import yt_dlp
    except Exception:
        raise RuntimeError("没装 yt-dlp")
    d = _deno()
    lg = _YtLog()
    opts = {"quiet": True, "no_warnings": True, "noprogress": True, "noplaylist": True, "socket_timeout": 20,
            "logger": lg, "js_runtimes": {"deno": {"path": d} if d else {}}}
    if flat:
        opts["extract_flat"] = "in_playlist"
    if download:
        TMP_DIR.mkdir(parents=True, exist_ok=True)
        opts.update({"format": "bestaudio/best", "outtmpl": str(TMP_DIR / "yt_%(id)s.%(ext)s"),
                     "max_filesize": MAX_BYTES * 2, "overwrites": True})
    try:
        with yt_dlp.YoutubeDL(opts) as y:
            info = y.extract_info(target, download=download)
            if download and isinstance(info, dict):
                rd = info.get("requested_downloads") or [{}]
                info["_path"] = rd[0].get("filepath") or y.prepare_filename(info)
    except Exception as e:
        raise RuntimeError(_yt_why(lg.err or e))
    if not isinstance(info, dict):
        raise RuntimeError(_yt_why(lg.err or "没拿到视频信息"))
    return info


def yt_search(q, n=8):
    """YouTube 搜 → [{"id", "bvid"(同 id，好跟 B站 共用排序), "title", "up", "play", "dur"}]"""
    info = _yt_extract(f"ytsearch{int(n)}:{q}", flat=True)
    out = []
    for e in info.get("entries") or []:
        if not isinstance(e, dict) or not e.get("id"):
            continue
        out.append({"id": str(e["id"]), "bvid": str(e["id"]), "title": str(e.get("title") or ""),
                    "up": str(e.get("channel") or e.get("uploader") or ""), "play": int(e.get("view_count") or 0),
                    "dur": float(e.get("duration") or 0)})
    return out


def yt_rank(results, title, artist, raw_req="", rejected=None, studio=False, bad=()):
    """YouTube 搜到的按「像原唱」排：跟 B站 一样过 check_title（英文 cover / remix / karaoke / sped up / nightcore / 8d 不要）；
    「歌手 - Topic」频道（YouTube 自动生成的官方音频 +10：排在官方 MV、歌词版前面）、VEVO +3；要换声时 live 排最后"""
    res = []
    for x in results or []:
        if not isinstance(x, dict):
            continue
        up = str(x.get("up") or "")
        bonus, bw = 0, ""
        if re.search(r"\s-\s*topic$", up, re.I):
            bonus, bw = 10, "、是官方自动生成的音频频道（Topic：干净的录音室音频 比 MV 好）"
        elif re.search(r"vevo$", up, re.I):
            bonus, bw = 3, "、是 VEVO 官方频道"
        res.append(dict(x, bvid=x.get("bvid") or x.get("id"), bonus=bonus, bonus_why=bw))
    return bili_rank(res, title, artist, raw_req, rejected, studio, bad)


def yt_download(vid):
    """下一个 YouTube 视频的音频 → (文件路径, info)；出错抛 RuntimeError"""
    info = _yt_extract(f"https://www.youtube.com/watch?v={vid}", download=True)
    p = Path(str(info.get("_path") or ""))
    if not p.is_file() or p.stat().st_size < 10000:
        raise RuntimeError("音频没下下来")
    return p, info


_TS = re.compile(r"\[(\d{1,2}):(\d{1,2}(?:[.:]\d{1,3})?)\]")
CREDIT = re.compile(r"(作词|作曲|编曲|制作|词|曲|演唱|原唱|混音|录音|和声|吉他|贝斯|鼓|弦乐|监制|出品|OP|SP)\s*[:：]", re.I)


def chorus_from_lrc(lrc, dur=0):
    """歌词时间轴 → 副歌开始的秒数（重复最多的那几句第一次出现在前奏和第一段主歌之后的地方）；找不到 None"""
    lines = []
    for raw in (lrc or "").splitlines():
        stamps = _TS.findall(raw)
        text = _TS.sub("", raw).strip()
        if not stamps or not text or CREDIT.search(text[:12]):
            continue
        n = _norm(text)
        if len(n) < 3:
            continue
        for mm, ss in stamps:
            lines.append((int(mm) * 60 + float(ss.replace(":", ".")), n))
    if len(lines) < 6:
        return None
    lines.sort()
    count = {}
    for _, n in lines:
        count[n] = count.get(n, 0) + 1
    rep = {n for n, c in count.items() if c >= 2}
    if not rep:
        return None
    top = max(count[n] for n in rep)
    best = [n for n in rep if count[n] == top]
    total = dur or (lines[-1][0] + 20)
    for i, (t, n) in enumerate(lines):
        if n in best and t >= 0.18 * total:
            j = i                               # 往前找：同一段副歌里前面几句也是重复的 从第一句开始
            while j > 0 and lines[j - 1][1] in rep and t - lines[j - 1][0] < 25 and i - j < 4:
                j -= 1
            return max(0.0, lines[j][0] - 0.3)
    return max(0.0, next(t for t, n in lines if n in best) - 0.3)


# ───────────── B站 ─────────────
def _strip_html(t):
    return re.sub(r"<[^>]+>", "", str(t or ""))


def _dur_secs(d):
    if isinstance(d, (int, float)):
        return float(d)
    p = [int(x) for x in re.findall(r"\d+", str(d or ""))]
    return float(p[-3] * 3600 + p[-2] * 60 + p[-1]) if len(p) >= 3 else float(p[-2] * 60 + p[-1]) if len(p) == 2 else 0.0


async def bili_audio(c, params, page=1):
    """B站视频 → (音轨 bytes, 视频信息)。失败抛 RuntimeError（中文原因）"""
    import bili2
    v, why = None, ""
    for url, signed in (("https://api.bilibili.com/x/web-interface/wbi/view", True),
                        ("https://api.bilibili.com/x/web-interface/view", False)):
        try:
            r = await c.c.get(url + "?" + c.signed(params)) if signed else await c.c.get(url, params=params)
            j = bili2._j(r, "视频信息")
            if j.get("code") == 0 and j.get("data"):
                v = j["data"]
                break
            why = j.get("message") or j.get("code")
        except Exception as e:
            why = e
    if not v:
        raise RuntimeError(f"读不到这个视频（{why}）")
    pages = v.get("pages") or []
    cid = (pages[page - 1].get("cid") if 0 < page <= len(pages) else None) or v.get("cid")
    bvid = v.get("bvid")
    pj = None
    for url, signed in (("https://api.bilibili.com/x/player/wbi/playurl", True),
                        ("https://api.bilibili.com/x/player/playurl", False)):
        q = {"bvid": bvid, "cid": cid, "fnval": 16, "fnver": 0, "fourk": 1}
        try:
            r = await c.c.get(url + "?" + c.signed(q)) if signed else await c.c.get(url, params=q)
            j = bili2._j(r, "播放地址")
            if j.get("code") == 0 and ((j.get("data") or {}).get("dash") or {}).get("audio"):
                pj = j
                break
            why = j.get("message") or j.get("code")
        except Exception as e:
            why = e
    if not pj:
        raise RuntimeError(f"拿不到音轨（{why}）")
    best = sorted(pj["data"]["dash"]["audio"], key=lambda x: -int(x.get("bandwidth") or 0))[0]
    urls = [best.get("baseUrl") or best.get("base_url")] + list(best.get("backupUrl") or best.get("backup_url") or [])
    for u in [x for x in urls if x]:
        try:
            buf = bytearray()
            async with c.c.stream("GET", u, headers={"Referer": f"https://www.bilibili.com/video/{bvid}/",
                                                     "Accept": "*/*", "Range": "bytes=0-"}) as rr:
                if rr.status_code not in (200, 206):
                    why = f"HTTP {rr.status_code}"
                    continue
                async for ch in rr.aiter_bytes():
                    buf += ch
                    if len(buf) > MAX_BYTES * 2:
                        raise RuntimeError("音轨太大了（超过 80MB）")
            if len(buf) > 10000:
                return bytes(buf), v
            why = "下下来是空的"
        except RuntimeError:
            raise
        except Exception as e:
            why = repr(e)[:80]
    raise RuntimeError(f"音轨下不下来（{why}）")


async def bili_search(c, kw):
    await asyncio.sleep(0.3)
    import bili2
    import urllib.parse
    hdr = {"Referer": "https://search.bilibili.com/all?keyword=" + urllib.parse.quote(kw),
           "Origin": "https://search.bilibili.com"}
    j = bili2._j(await c.c.get("https://api.bilibili.com/x/web-interface/wbi/search/type?"
                               + c.signed({"search_type": "video", "keyword": kw, "page": 1, "page_size": 20,
                                           "platform": "pc", "web_location": 1430654}), headers=hdr), "搜索")
    return [{"bvid": x.get("bvid"), "title": _strip_html(x.get("title")), "up": x.get("author") or "",
             "play": int(x.get("play") or 0), "dur": _dur_secs(x.get("duration"))}
            for x in ((j.get("data") or {}).get("result") or [])]


def bili_pick(results, title, artist, raw_req=""):
    """搜到的视频里挑「就是这首歌的原唱」那个（最像的一个；没有 None）"""
    ranked = bili_rank(results, title, artist, raw_req)
    return ranked[0] if ranked else None


def bili_rank(results, title, artist, raw_req="", rejected=None, studio=False, bad=()):
    """搜到的视频按「像原唱」排好（10-29 第 8 件：每一条都过 check_title 核对标题）：
    标题里有歌名；点了歌手的 标题或 UP 主里有歌手；不要 混剪 MV 字幕组 动画 合集 翻唱 伴奏 教学；歌名不能只是顺带提到；
    带 原唱 / 官方 / live / 歌词版 的排前面；视频本身不到 MIN_REAL_SEC 秒或超过 10 分钟的不要
    → 第一个放不了（地区限制、拿不到音轨）就试下一个（10-26 晚）
    rejected（list，可不给）：核对掉的每条追加 (标题, 原因)；返回的每一项多一个 "why"（为什么选它）"""
    out = []
    bad = {str(b) for b in bad or ()}
    for i, x in enumerate(results or []):
        if not isinstance(x, dict):
            continue
        t = str(x.get("title") or "")
        if str(x.get("bvid") or "") in bad:
            if rejected is not None:
                rejected.append((t, "有人说过这个版本不是他要的"))
            continue
        ok, sc, why = check_title(t, x.get("up") or "", title, artist, raw_req)
        if ok and x.get("dur") and (x["dur"] < MIN_REAL_SEC or x["dur"] > 600):
            ok, why = False, f"视频时长 {x['dur']:.0f} 秒（要 {MIN_REAL_SEC}~600 秒）"
        if not ok:
            if rejected is not None:
                rejected.append((t, why))
            continue
        sc += len(str(x.get("play") or 0))                              # 播放量的位数（数量级）
        if x.get("bonus"):                                              # YouTube 的 Topic / VEVO 官方频道（11-01 v1.16.0）
            sc += int(x["bonus"])
            why += str(x.get("bonus_why") or "")
        live = bool(studio and LIVE.search(t))
        if live:                                                        # 要换声：现场版排到所有录音室版后面（10-31 v1.15.14 只扣 8 分
            why += "（要换声 现场版排最后）"                              #   「live」又在 PREFER_STRONG 里 +6 → 净扣 2 分 播放量一高就翻盘 11-01 v1.15.18）
        out.append((live, sc, i, dict(x, why=why, score=sc, live=live)))
    out.sort(key=lambda z: (z[0], -z[1], z[2]))    # 不是现场版的在前；再按分高；一样高的按 B站 给的顺序
    return [x for _, _, _, x in out]


# ───────────── 拿不准是哪首（C7 歧义度 阶段 0，10-30：只记日志、照常唱第一个） ─────────────
AMB_GAP = 1          # B站：第一第二的分差 ≤ 这个 = 拉不开


def _rest(vtitle, title):
    """标题去掉歌名、【】（）装饰、原唱 / 官方 / 高音质这类词、书名号以后剩下的（多半是歌手 / 版本）"""
    vt = _simp(str(vtitle or ""))
    rest = _sn(re.sub(r"[【\[（(][^】\]）)]*[】\]）)]", " ", vt))
    for v in title_variants(title):
        rest = rest.replace(v, "")
    for w in PREFER_STRONG.findall(vt) + PREFER.findall(vt):
        rest = rest.replace(_sn(w), "")
    return re.sub(r"[《》「」『』\-—_|/·:：]", "", rest)


def ambiguity(src, cands, title, artist=""):
    """点歌没说歌手、候选里第一第二拉不开又不像同一首 → {"src", "gap", "options": [...]}（以后会问「要 A 的还是 B 的」）；
    拉得开 / 说了歌手 / 像同一首 → None。src = "网易云"（看歌手）/ "B站"（看分差 + 标题里剩下的字）"""
    if artist or len(cands or []) < 2:
        return None
    if src in ("网易云", "QQ音乐", "酷狗"):      # 歌曲平台都看前三个歌手（11-01 v1.16.0）；B站 / YouTube 看分差
        arts = []
        for c in cands[:3]:                # 只看前三（再往后多半是翻唱）
            a = "、".join(c.get("artists") or [])
            if a and a not in arts:
                arts.append(a)
        return {"src": src, "gap": 0, "options": arts[:3]} if len(arts) >= 2 else None
    a, b = cands[0], cands[1]
    gap = float(a.get("score") or 0) - float(b.get("score") or 0)
    if gap > AMB_GAP:
        return None
    ra, rb = _rest(a.get("title"), title), _rest(b.get("title"), title)
    if not ra or not rb or ra in rb or rb in ra or _sn(a.get("up")) == _sn(b.get("up")):
        return None
    opts = [str(x.get("title") or "")[:40] for x in cands[:3]
            if float(a.get("score") or 0) - float(x.get("score") or 0) <= AMB_GAP]
    return {"src": src, "gap": gap, "options": opts}


def note_ambiguity(amb, title, req, log=print):
    """阶段 0：记一行 [歧义] + data/ambiguity/日期.jsonl（不记是谁点的）"""
    if not amb:
        return
    log(f"[歧义] 点「{str(title)[:20]}」没说歌手：{amb['src']}里拉不开（{' / '.join(o[:20] for o in amb['options'])}）"
        f"→ 没让问（ask=False），照常用第一个")
    try:
        import shadowlog
        shadowlog.write("ambiguity", {"what": "点歌", "title": str(title)[:40], "req": str(req or "")[:40], **amb})
    except Exception:
        pass


# ───────────── 找高潮 / 剪 ─────────────
def _ff():
    return shutil.which("ffmpeg")


# ───────────── 唱整首（11-01 v1.16.5）：同一套找歌 拿到完整音频后不剪 ─────────────
_FULL = threading.local()          # 这个线程正在找整首吗（sing.render_full 在后台线程里 with full_mode(…) 调 fetch）


class full_mode:
    """with full_mode(360): fetch(…) → 整首（不剪 只去头尾静音、超过 360 秒截到 360）"""

    def __init__(self, max_sec):
        self.max_sec = float(max_sec or 360)

    def __enter__(self):
        self.old = getattr(_FULL, "max", 0)
        _FULL.max = self.max_sec
        return self

    def __exit__(self, *a):
        _FULL.max = self.old


def _full_max():
    return float(getattr(_FULL, "max", 0) or 0)


def _make_full(src, out, max_sec):
    """完整音频 → 只去掉头尾的静音、超过上限就截到上限（最后 3 秒淡出）→ 24k 单声道 wav → (0.0, 秒数, "整首") / None"""
    ff = _ff()
    if not ff:
        return None
    rs = real_seconds(src)
    sil = "silenceremove=start_periods=1:start_duration=0.2:start_threshold=-50dB"
    af = f"aformat=channel_layouts=mono,aresample=24000,{sil},areverse,{sil},areverse,asetpts=N/SR/TB"
    if rs and rs > max_sec + 1:
        af += f",atrim=end={max_sec:.2f},afade=t=out:st={max(0.0, max_sec - 3):.2f}:d=3"
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src), "-vn", "-af", af, "-ac", "1", "-ar", "24000",
           "-sample_fmt", "s16", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=max(120, int((rs or max_sec) * 2)), creationflags=NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0 or not Path(out).exists() or Path(out).stat().st_size < 2000:
        return None
    length = real_seconds(out) or min(rs or max_sec, max_sec)
    return 0.0, round(float(length), 1), "整首"


def loudest(path, sec, dur=0):
    """按响度找最响的一段（避开前奏和结尾）→ 开始秒数"""
    ff = _ff()
    if not ff:
        return 0.0
    try:
        import numpy as np
        r = subprocess.run([ff, "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", "8000", "-f", "s16le", "-"],
                           capture_output=True, timeout=120, creationflags=NO_WINDOW)
        a = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32)
    except Exception:
        return 0.0
    if not len(a):
        return 0.0
    hop = 2000                                                       # 0.25 秒一帧
    n = len(a) // hop
    if n < 8:
        return 0.0
    e = np.sqrt((a[:n * hop].reshape(n, hop) ** 2).mean(axis=1))
    w = max(1, int(sec * 4))
    if n <= w:
        return 0.0
    cs = np.concatenate([[0.0], np.cumsum(e)])
    win = cs[w:] - cs[:-w]
    total = n / 4.0
    lo, hi = int(0.2 * total * 4), int(max(0.2, 0.85 * total - sec) * 4)
    if hi <= lo or hi > len(win):
        lo, hi = 0, len(win)
    i = int(np.argmax(win[lo:hi])) + lo
    return i / 4.0


def pick_clip(path, sec, dur=0):
    """找副歌（10-31 v1.15.14 小明：「截取逻辑不够好」—— 以前只挑最响的 12 秒，常常从半句开始、半句结束）
    → (开始秒, 长度秒, 怎么挑的)；出错 / 太短 → None（外面退回 loudest）
    ① 和声特征（chroma，12 个音名）每半秒一帧 → 自相似：整首歌里「这一段在别处又完整出现过」越像越可能是副歌
    ② 响度（副歌一般比主歌响）→ ①② 加起来挑开头（避开前 8% 和最后 8%）
    ③ 对齐句子：开头往前挪到最近的换气（响度低谷，最多往前 2.5 秒）、结尾往后挪到下一个低谷（最多多 4 秒）"""
    ff = _ff()
    if not ff:
        return None
    try:
        import numpy as np
        sr = 11025
        r = subprocess.run([ff, "-loglevel", "error", "-i", str(path), "-vn", "-ac", "1", "-ar", str(sr), "-f", "s16le", "-"],
                           capture_output=True, timeout=120, creationflags=NO_WINDOW)
        a = np.frombuffer(r.stdout, dtype="<i2").astype(np.float32) / 32768.0
        total = len(a) / sr
        if total < max(40.0, sec * 2.5):
            return None
        hop, win = sr // 10, 2048                                    # 0.1 秒一帧
        n = (len(a) - win) // hop
        idx = np.arange(win)[None, :] + hop * np.arange(n)[:, None]
        fr = a[idx] * np.hanning(win)[None, :]
        rms = np.sqrt((fr ** 2).mean(axis=1)) + 1e-6
        mag = np.abs(np.fft.rfft(fr, axis=1))
        freqs = np.fft.rfftfreq(win, 1 / sr)
        band = (freqs >= 80) & (freqs <= 4000)
        pc = np.round(12 * np.log2(freqs[band] / 440.0)).astype(int) % 12
        chroma = np.zeros((n, 12), np.float32)
        for k in range(12):
            chroma[:, k] = mag[:, band][:, pc == k].sum(axis=1)
        g = 5                                                        # 半秒一帧
        m = n // g
        C = chroma[:m * g].reshape(m, g, 12).mean(axis=1)
        C = np.log1p(C * 10)
        C = C - C.mean(axis=1, keepdims=True)
        C /= np.linalg.norm(C, axis=1, keepdims=True) + 1e-9
        E = np.log(rms[:m * g].reshape(m, g).mean(axis=1))
        w = max(4, int(round(sec * 2)))
        if m < w * 3:
            return None
        S = C @ C.T
        rep = np.full(m - w + 1, -1.0)
        for L in range(w, m - w + 1):                                # 两段至少隔一整段（不算跟自己挨着的重复）
            d = np.diagonal(S, L)
            cs = np.concatenate([[0.0], np.cumsum(d)])
            mv = (cs[w:] - cs[:-w]) / w                              # 长度 m-L-w+1：从 i 开始的这段 跟 i+L 开始的那段像不像
            k = len(mv)
            rep[:k] = np.maximum(rep[:k], mv)                        # 前面这段在后面又出现
            rep[L:L + k] = np.maximum(rep[L:L + k], mv)              # 后面这段在前面出现过
        ce = np.concatenate([[0.0], np.cumsum(E)])
        loud = (ce[w:] - ce[:-w]) / w

        def z(x):
            return (x - x.mean()) / (x.std() + 1e-9)
        score = z(rep) + 0.7 * z(loud)
        lo, hi = int(0.08 * m), int(0.92 * m) - w
        if hi <= lo:
            lo, hi = 0, len(score)
        best = float(score[lo:hi].max())
        peaks = [j for j in range(lo, hi) if score[j] >= score[max(lo, j - 16):j + 17].max()]   # ±8 秒里最高的点
        i = min([j for j in peaks if score[j] >= best - 0.25] or [int(np.argmax(score[lo:hi])) + lo])   # 差不多一样像副歌的 取最早那次
        st = i / 2.0
        # ③ 对齐句子：响度（0.1 秒一帧 平滑过）的低谷
        sm = np.convolve(np.log(rms), np.ones(3) / 3, mode="same")

        def dip(a_s, b_s):
            a_i, b_i = max(0, int(a_s * 10)), min(len(sm) - 1, int(b_s * 10))
            if b_i <= a_i:
                return None
            return (a_i + int(np.argmin(sm[a_i:b_i + 1]))) / 10.0
        s2 = dip(st - 2.5, st + 0.3)
        st = s2 if s2 is not None else st
        e2 = dip(st + sec - 0.5, min(total - 0.5, st + sec + 4.0))
        end = e2 if e2 is not None else st + sec
        length = max(sec * 0.8, min(sec + 4.0, end - st))
        rz = float(z(rep)[i])
        return round(max(0.0, st), 2), round(length, 2), ("副歌（整首里重复出现的一段）" if rz > 0.8 else "又响又重复的一段")
    except Exception:
        return None


def cut(src, start, sec, out):
    ff = _ff()
    if not ff:
        return None
    fo = min(1.5, sec / 4)
    cmd = [ff, "-y", "-hide_banner", "-loglevel", "error", "-ss", f"{max(0.0, start):.2f}", "-t", f"{sec:.2f}",
           "-i", str(src), "-vn", "-ac", "1", "-ar", "24000", "-sample_fmt", "s16",
           "-af", f"afade=t=in:st=0:d=0.3,afade=t=out:st={sec - fo:.2f}:d={fo:.2f}", str(out)]
    try:
        r = subprocess.run(cmd, capture_output=True, timeout=120, creationflags=NO_WINDOW)
    except Exception:
        return None
    if r.returncode != 0 or not out.exists() or out.stat().st_size < 2000:
        return None
    return out


# ───────────── 记过的 ─────────────
def _cache():
    try:
        d = json.loads(CACHE.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return {k: v for k, v in d.items() if isinstance(v, dict)} if isinstance(d, dict) else {}


def _save_cache(d):
    try:
        CACHE.parent.mkdir(parents=True, exist_ok=True)
        tmp = CACHE.with_suffix(".tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False, indent=1), encoding="utf-8")
        tmp.replace(CACHE)
    except Exception:
        pass


def _key(title, artist):
    return _norm(title) + "|" + _norm(artist)


# ───────────── 来源顺序 / 能不能用（11-01 v1.16.0）─────────────
def _foreign(t):
    """歌名是不是外文：有假名 / 韩文 → 是；有汉字 → 不是；只有拉丁 / 西里尔字母 → 是"""
    t = str(t or "")
    if re.search(r"[぀-ヿ가-힯]", t):
        return True
    if re.search(r"[一-鿿]", t):
        return False
    return bool(re.search(r"[A-Za-zÀ-ɏЀ-ӿ]", t))


def _cfg_sources():
    """config sing.sources（先看 sing.cfg()，再看 config.json；写错的、重复的去掉）；没写 / 全写错 → 默认顺序"""
    lst = SOURCES
    if lst is None:
        try:
            s = sys.modules.get("sing")
            c = s.cfg() if s is not None and hasattr(s, "cfg") else {}
            lst = c.get("sources") if isinstance(c, dict) else None
        except Exception:
            lst = None
    if lst is None:
        try:
            c = json.loads((ROOT / "config.json").read_text(encoding="utf-8"))
            lst = ((c or {}).get("sing") or {}).get("sources")
        except Exception:
            lst = None
    out = []
    for x in lst if isinstance(lst, (list, tuple)) else []:
        k = SRC_KEY.get(str(x).strip(), str(x).strip().lower())
        if k in SRC_NAME and k not in out:
            out.append(k)
    return out or list(DEFAULT_SOURCES)


def source_order(title=""):
    """这首歌按什么顺序找（配置的顺序；歌名是外文的 YouTube 提到 B站 前面）"""
    lst = _cfg_sources()
    if _foreign(title) and "youtube" in lst and "bili" in lst and lst.index("youtube") > lst.index("bili"):
        lst.remove("youtube")
        lst.insert(lst.index("bili"), "youtube")
    return lst


def _src_state(src):
    """一个来源现在能不能用 → (能用, 原因, 登录了没有 / None = 这个来源不用登录)。不联网"""
    if src in ("qq", "kugou"):
        if _logged(src):
            return True, "", True
        return False, f"{SRC_NAME[src]}没登录（要在控制台「唱歌」页扫码登录主人的账号）", False
    if src == "netease":
        lg = _logged("netease")
        if ne_abroad():
            return False, "网易云把这台电脑当海外 IP（搜索只给加密结果 过几个小时再试）", lg
        return True, ("" if lg else "没登录：只能放免费歌"), lg
    if src == "youtube":
        ok, w = _yt_ready()
        return ok, w, None
    return True, "", None


def sources_status():
    """各来源的状态（控制台「唱歌」页 / 开机自检用；不联网）→ {source: {"enabled", "ok", "why", "name", "login", "order"}}
    enabled = 在 sing.sources 里；ok = 现在用得了；why = 用不了的原因（能用但有限制的也写，比如网易云没登录只能放免费歌）"""
    en = _cfg_sources()
    out = {}
    for src in DEFAULT_SOURCES:
        try:
            ok, why, lg = _src_state(src)
        except Exception as e:
            ok, why, lg = False, f"查不了（{repr(e)[:50]}）", None
        if src not in en:
            why = "没放进 sing.sources（不用这个来源）" + (f"；{why}" if why else "")
        out[src] = {"enabled": src in en, "ok": bool(ok), "why": why, "name": SRC_NAME[src], "login": lg,
                    "order": en.index(src) if src in en else None}
    return out


def _active(title, log=print):
    """这次能用的来源（按顺序）+ 跳过的（「QQ音乐：没登录…」）；跳过的变了才记一行日志"""
    order, skipped = [], []
    for src in source_order(title):
        try:
            ok, w, _ = _src_state(src)
        except Exception as e:
            ok, w = False, f"查不了（{repr(e)[:50]}）"
        if ok:
            order.append(src)
        else:
            skipped.append(f"{SRC_NAME[src]}：{w}")
    s = "；".join(skipped)
    if s != _TOLD["skip"]:
        _TOLD["skip"] = s
        if s:
            log(f"[唱歌] 这几个来源先跳过：{s}")
    return order, skipped


def _plat_filter(raw, title, artist):
    """歌曲平台搜到的：歌名完全对上、不是伴奏翻唱、点了歌手的歌手要对上；现场版排后面（其余照平台给的顺序）"""
    out = [s for s in raw or [] if isinstance(s, dict) and s.get("name")
           and _ok_name(s["name"], title, f"{title} {artist}") and _artist_ok(s.get("artists") or [], artist)]
    out.sort(key=lambda s: bool(LIVE.search(str(s["name"]))))
    return out


def _search(src, title, artist, req, sessdata="", studio=False, bad=(), log=print, rej=None):
    """一个来源搜一次 → (搜到的原始结果, 排好筛好的候选)。出错抛 RuntimeError（中文原因）"""
    q = f"{title} {artist}".strip()
    badset = {str(b) for b in bad or ()}
    if src == "netease":
        c = ne_search(title, artist, None, log)
        return c, [s for s in c if str(s.get("id")) not in badset]
    if src in ("qq", "kugou"):
        raw = qq_search_raw(q) if src == "qq" else kg_search_raw(q)
        return raw, [s for s in _plat_filter(raw, title, artist) if str(s["id"]) not in badset]
    if src == "bili":
        res = asyncio.run(_bili_search_only(q, sessdata))
        return res, bili_rank(res, title, artist, req, rej, studio, bad)
    if src == "youtube":
        res = yt_search(q)
        return res, yt_rank(res, title, artist, req, rej, studio, bad)
    raise RuntimeError("不认识的来源")


# ───────────── 拿不准就问（11-01 v1.16.0：fetch(ask=True)）─────────────
def _ask_options(src, cands, title):
    """候选 → 给她看的 2~3 个选项（label 是人一眼看得懂的「歌手《歌名》」/「B站：视频标题」）"""
    nm = SRC_NAME[src]
    opts = []
    if src in SONG_SRC:
        seen = set()
        for c in cands[:3]:
            a = "、".join(c.get("artists") or [])
            if not a or a in seen:
                continue
            seen.add(a)
            opts.append({"label": f"{a}《{c.get('name')}》", "source": src, "id": c.get("id"), "title": c.get("name") or title,
                         "artist": a, "video": "", "up": "", "dur": c.get("dur") or 0,
                         "x": {k: v for k, v in (c.get("x") or {}).items() if k != "lyric"}})
    else:
        top = float(cands[0].get("score") or 0) if cands else 0
        for c in cands[:3]:
            if top - float(c.get("score") or 0) > AMB_GAP:
                continue
            opts.append({"label": f"{nm}：{str(c.get('title') or '')[:40]}", "source": src, "id": c.get("bvid") or c.get("id"),
                         "title": title, "artist": "", "video": str(c.get("title") or "")[:120], "up": c.get("up") or "",
                         "dur": c.get("dur") or 0})
    return opts[:3]


def _probe(order, title, req, sessdata, studio, bad, log, pre):
    """第一个搜得到候选的来源说了算：拿不准 → {"req", "src", "title", "options"}；拿得准 → None。搜到的放进 pre（后面不用再搜一遍）"""
    for src in order:
        try:
            raw, cands = _search(src, title, "", req, sessdata, studio, bad, log)
        except Exception as e:
            log(f"[唱歌] 看拿不准时 {SRC_NAME[src]} 搜不了（{str(e)[:60]}）")
            continue
        pre[src] = raw if src == "bili" else cands
        if not cands:
            continue
        if not ambiguity(SRC_NAME[src], cands, title, ""):
            return None
        opts = _ask_options(src, cands, title)
        if len(opts) < 2:
            return None
        log(f"[唱歌] 点「{req}」没说歌手、{SRC_NAME[src]}里拉不开：" + " / ".join(o["label"] for o in opts) + " → 先问是哪个（不下载）")
        return {"req": req, "src": src, "title": title, "options": opts}
    return None


# ───────────── 入口 ─────────────
def fetch(req, log=print, sec=12, sessdata="", studio=False, *, ask=False, _nofb=False):
    """点的歌 → {"path", "title", "artist", "source"(中文名), "src"(key), "id", "start", …}；找不到 / 放不了 → {"fail": 原因, "skipped": [...]}
    ask=True 且拿不准是哪首（同名歌没说歌手、前几名拉不开又不是同一首）→ {"ask": {"req", "src", "title", "options": [opt…]}}（没下载）
    11-01 v1.16.0：来源按 source_order 一个个试（没登录 / 用不了的跳过）"""
    title, artist = parse(req)
    if not _norm(title):
        return {"fail": "没说是哪首"}
    if not _ff():
        return {"fail": "没装 ffmpeg 剪不了"}
    key = _key(title, artist)
    cache = _cache()
    bad = list((cache.get("_bad") or {}).get(key) or []) + list((cache.get("_bad") or {}).get(_key(title, "")) or [])
    if _full_max():                        # 唱整首（11-01 v1.16.5）：记在「full|…」下（别跟剪好的一段混了）
        key = "full|" + key
        hit = cache.get(key)
    else:
        hit = cache.get(key) or (cache.get(_key(title, "")) if not artist else None)
    if hit and hit.get("how") == "最响的一段" and int(hit.get("cut_v") or 1) < CUT_V:
        log(f"[唱歌] 「{req}」以前找过 但那段是老剪法（最响的 {sec} 秒 常常半句开始）→ 重新找、重新剪")
        hit = None
    if hit and (CLIP_DIR / hit.get("clip", "")).is_file():
        okc, whyc = _recheck_cached(hit, title, artist, req, studio, bad)
        if okc:
            log(f"[唱歌] 「{req}」以前找过：{hit.get('source')}《{hit.get('title')}》{hit.get('artist')}"
                + (f"（当时选的：{hit.get('pick_why')}）" if hit.get("pick_why") else ""))
            return dict(hit, path=CLIP_DIR / hit["clip"])
        log(f"[唱歌] 「{req}」以前找的 {hit.get('source')}《{str(hit.get('video') or hit.get('title'))[:40]}》按现在的规矩核对不过（{whyc}）→ 丢掉重新找")
        for k in [k for k, v in cache.items() if v is hit]:
            cache.pop(k, None)
        _save_cache(cache)
    order, skipped = _active(title, log)
    if not order:
        return {"fail": "没有能用的来源（" + "；".join(skipped) + "）", "skipped": skipped}
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    tag = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    out = CLIP_DIR / (f"full_net_{tag}.wav" if _full_max() else f"song_net_{tag}.wav")
    why = []
    pre = {}
    if ask and not artist:
        try:
            q = _probe(order, title, req, sessdata, studio, bad, log, pre)
        except Exception as e:                   # 问不了就照常找（别让唱歌整个挂掉）
            log(f"[唱歌] 看拿不准出错（{repr(e)[:80]}）→ 照常用第一个")
            q = None
        if q:
            return {"ask": q}
    for src in order:
        got = None
        try:
            if src == "netease":                 # 网易云这边出什么错都别让唱歌整个挂掉 接着找下一个来源
                got = _ne_fetch(title, artist, req, sec, out, cache, key, why, log, bad,
                                **({"pre": pre["netease"]} if "netease" in pre else {}))
            elif src == "bili":
                meta = asyncio.run(_bili_fetch(title, artist, req, sec, out, sessdata, log, studio, bad,
                                               **({"pre": pre["bili"]} if "bili" in pre else {})))
                if meta:
                    cache[key] = meta
                    _save_cache(cache)
                    got = dict(meta, path=out)
            elif src in ("qq", "kugou"):
                got = _plat_fetch(src, title, artist, req, sec, out, cache, key, why, log, bad, pre.get(src))
            elif src == "youtube":
                got = _yt_fetch(title, artist, req, sec, out, cache, key, why, log, bad, studio, pre.get(src))
        except RuntimeError as e:
            why.append(f"{SRC_NAME[src]}：{e}")
        except Exception as e:
            why.append(f"{SRC_NAME[src]}出错：{repr(e)[:80]}")
        if got:
            return got
    if artist and not _nofb:               # 她写的歌手可能写错了（11-01 v1.15.18 借 chorus-cover 0.17.2 的思路）：去掉歌手再找一次
        log(f"[唱歌] 「{req}」带着歌手「{artist}」没找到 → 去掉歌手再找一次（{'；'.join(why[-2:])[:80]}）")
        got = fetch(title, log, sec, sessdata, studio, _nofb=True)
        if got.get("path"):
            got["pick_why"] = (str(got.get("pick_why") or "") + f"（她说的歌手「{artist}」找不到 去掉歌手找的）").strip()
            return got
    log(f"[唱歌] 网上找「{req}」没找到能唱的：" + "；".join(why[-4:]))
    return {"fail": "；".join(why[-3:]) or "没找到", "skipped": skipped}


def fetch_pick(opt, log=print, sec=12, sessdata="", studio=False):
    """他从 {"ask"} 的选项里选了一个 → 下这一个（成功 / 失败同 fetch，成功多 "picked": True）
    歌曲平台那首放不了（VIP / 地区）→ 同一首（歌名 + 歌手）去别的来源找"""
    o = opt if isinstance(opt, dict) else {}
    src = SRC_KEY.get(str(o.get("source") or ""), str(o.get("source") or ""))
    if src not in SRC_NAME or not o.get("id"):
        return {"fail": "选项不完整（没有来源或 id）"}
    if not _ff():
        return {"fail": "没装 ffmpeg 剪不了"}
    nm = SRC_NAME[src]
    title, artist = str(o.get("title") or ""), str(o.get("artist") or "")
    req = str(o.get("label") or title)
    key = _key(title, artist) if (artist and src in SONG_SRC) else f"pick|{src}|{o['id']}"
    if _full_max():                        # 唱整首（11-01 v1.16.5）
        key = "full|" + key
    cache = _cache()
    hit = cache.get(key)
    if hit and str(hit.get("id")) == str(o["id"]) and (CLIP_DIR / hit.get("clip", "")).is_file():
        log(f"[唱歌] 他选的「{req}」以前下过 → 直接用")
        return dict(hit, path=CLIP_DIR / hit["clip"], picked=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    out = CLIP_DIR / f"{'full' if _full_max() else 'song'}_net_{hashlib.md5(key.encode('utf-8')).hexdigest()[:10]}.wav"
    why, got = [], None
    try:
        if src in SONG_SRC:
            s = {"id": o["id"], "name": title, "artists": [a for a in artist.split("、") if a], "dur": float(o.get("dur") or 0),
                 "x": dict(o.get("x") or {})}
            got = _song_try(src, [s], title, req, sec, out, cache, key, why, log, tries=1)
        else:
            cand = {"title": o.get("video") or "", "up": o.get("up") or "", "why": "他从选项里选的"}
            if src == "bili":
                meta = asyncio.run(_bili_one(str(o["id"]), 1, title, "", req, sec, out, sessdata, log, cand))
            else:
                meta = _yt_one(str(o["id"]), title, "", req, sec, out, log, cand)
            cache[key] = meta
            _save_cache(cache)
            got = dict(meta, path=out)
    except RuntimeError as e:
        why.append(f"{nm}：{e}")
    except Exception as e:
        why.append(f"{nm}出错：{repr(e)[:80]}")
    if got:
        got["picked"] = True
        return got
    if src in SONG_SRC and title and artist:
        log(f"[唱歌] 他选的{req}在{nm}放不了（{'；'.join(why[-1:])[:60]}）→ 同一首去别的来源找")
        got = fetch(f"{title} - {artist.split('、')[0]}", log, sec, sessdata, studio, _nofb=True)
        if got.get("path"):
            got["pick_why"] = (str(got.get("pick_why") or "") + f"（他选的{req} 在{nm}放不了 别的来源找的同一首）").strip()
            got["picked"] = True
            return got
        why.append(str(got.get("fail") or ""))
    return {"fail": "；".join(w for w in why[-3:] if w) or "没下下来"}


def mark_bad(meta, why="", log=print):
    """有人说刚唱的不是这首 / 不是原唱 / 全是怪音（11-01 v1.15.18）：这个版本以后别再用 —— 记进 song_online.json 的 _bad，
    记着的那条删掉（下次点重新找、跳过它）→ 记上了没有"""
    meta = meta if isinstance(meta, dict) else {}
    vid = str(meta.get("id") or "")
    if not vid or not meta.get("title"):
        return False
    cache = _cache()
    b = cache.setdefault("_bad", {})
    keys = {_key(meta.get("title"), meta.get("artist") or ""), _key(meta.get("title"), "")}
    for k in keys:
        lst = b.setdefault(k, [])
        if vid not in lst:
            lst.append(vid)
        del lst[:-20]
    for k in [k for k, v in cache.items() if k != "_bad" and isinstance(v, dict) and str(v.get("id") or "") == vid]:
        cache.pop(k, None)
    _save_cache(cache)
    log(f"[唱歌] 有人说刚唱的《{meta.get('title')}》不对（{str(why)[:30]}）→ {meta.get('source')} {vid}「{str(meta.get('video') or '')[:30]}」以后不用、下次重新找")
    return True


def _ne_fetch(title, artist, req, sec, out, cache, key, why, log, bad=(), pre=None):
    """网易云：歌名对上的原唱（登录了是他账号能放的、没登录是免费的）→ 剪好 → {…, "path"}；不行返回 None（原因追加进 why）"""
    cands = ne_search(title, artist, None, log) if pre is None else list(pre)
    note_ambiguity(ambiguity("网易云", cands, title, artist), title, req, log)
    if not cands:
        why.append("网易云在海外用不了（只给加密的结果）" if ne_abroad() else "网易云没有歌名对得上的")
    cands = [s for s in cands if str(s.get("id")) not in {str(b) for b in bad}]     # 有人说过不对的版本（11-01 v1.15.18）
    return _song_try("netease", cands, title, req, sec, out, cache, key, why, log)


# ───────────── 歌曲平台共用（网易云 / QQ / 酷狗，11-01 v1.16.0）─────────────
def _song_audio(src, s):
    """一首 → (bytes, "", 扩展名) / (None, 原因, "")"""
    try:
        if src == "netease":
            _NE["why"] = ""
            a = ne_audio(s["id"], None)
            return (a, "", ".mp3") if a else (None, _NE.get("why") or "放不了（VIP 或者只给大陆播）", "")
        if src == "qq":
            return qq_audio(s)
        if src == "kugou":
            return kg_audio(s)
    except Exception as e:
        return None, f"出错（{repr(e)[:60]}）", ""
    return None, "不认识的来源", ""


def _song_lyric(src, s):
    """歌词（LRC）：网易云 / QQ 单独要，酷狗的在取播放地址时一起给了；拿不到 ""（退回听副歌）"""
    try:
        if src == "netease":
            return ne_lyric(s["id"], None)
        if src == "qq":
            return qq_lyric(s["id"])
        if src == "kugou":
            return str((s.get("x") or {}).get("lyric") or "")
    except Exception:
        pass
    return ""


def _make_clip(tmp, sec, out, dur=0, lrc=""):
    """整首 → 剪好的一段 → (开始秒, 长度, 怎么挑的)；剪不出来 None。有歌词先用歌词找副歌，没有再听（pick_clip），再不行最响的一段
    唱整首（full_mode 里）：不剪 → _make_full"""
    if _full_max():
        return _make_full(tmp, out, _full_max())
    st = chorus_from_lrc(lrc, dur) if lrc else None
    how, length = "歌词里的副歌", float(sec)
    if st is None:
        pc = pick_clip(tmp, sec, dur)
        st, length, how = pc if pc else (loudest(tmp, sec, dur), float(sec), "最响的一段")
    if not cut(tmp, st, length, out):
        return None
    return st, length, how


def _song_try(src, cands, title, req, sec, out, cache, key, why, log, tries=SONG_TRY):
    """歌曲平台的候选一首首试：拿音频（放不了换下一首、原因记进 why）→ 80 秒闸门 → 歌词找副歌 → 剪 → 记进 song_online.json"""
    nm = SRC_NAME[src]
    for s in cands[:tries]:
        audio, w, ext = _song_audio(src, s)
        if not audio:
            why.append(f"{nm}《{s.get('name')}》{w}")
            continue
        tmp = TMP_DIR / f"{src}_{hashlib.md5(str(s['id']).encode('utf-8')).hexdigest()[:10]}{ext or '.mp3'}"
        tmp.write_bytes(audio)
        try:
            rs = real_seconds(tmp)
            if rs is not None and rs < MIN_REAL_SEC:                  # 10-29 第 8 件：试听片段 / 预览
                why.append(f"{nm}《{s.get('name')}》下下来只有 {rs:.0f} 秒（试听片段 不到 {MIN_REAL_SEC} 秒不用）")
                log(f"[唱歌] {nm}《{s.get('name')}》下下来只有 {rs:.0f} 秒 当试听片段 不用")
                continue
            clip = _make_clip(tmp, sec, out, float(s.get("dur") or 0), _song_lyric(src, s))
            if not clip:
                why.append("剪不出来")
                continue
            st, length, how = clip
            meta = {"title": s.get("name") or title, "artist": "、".join(s.get("artists") or []), "source": nm, "src": src,
                    "sec": round(length, 1), "cut_v": CUT_V, "id": s["id"], "start": round(st, 1), "how": how,
                    "clip": out.name, "t": time.time()}
            if LIVE.search(str(s.get("name") or "")):
                meta["live_only"] = True        # 现场版排在最后还选了它 = 录音室版都放不了（下次核对别又丢掉）
            if key:
                cache[key] = meta
                _save_cache(cache)
            log(f"[唱歌] 网上找「{req}」→ {nm}《{meta['title']}》{meta['artist']}（{how} 从 {st:.0f} 秒开始 {length:.0f} 秒）")
            return dict(meta, path=out)
        finally:
            try:
                tmp.unlink()
            except OSError:
                pass
    return None


def _plat_fetch(src, title, artist, req, sec, out, cache, key, why, log, bad=(), pre=None):
    """QQ 音乐 / 酷狗：搜 → 筛 → 一首首试（同网易云）"""
    nm = SRC_NAME[src]
    if pre is None:
        try:
            raw, cands = _search(src, title, artist, req, bad=bad, log=log)
        except RuntimeError as e:
            why.append(f"{nm}{e}")
            return None
    else:
        raw, cands = pre, [s for s in pre if str(s.get("id")) not in {str(b) for b in bad}]
    if not cands:
        why.append(f"{nm}没有歌名对得上的" if raw else f"{nm}什么都没搜到")
        return None
    note_ambiguity(ambiguity(nm, cands, title, artist), title, req, log)
    return _song_try(src, cands, title, req, sec, out, cache, key, why, log)


# ───────────── 视频（B站 / YouTube）下一个、剪好 ─────────────
def _guess_title(vt):
    """视频标题里的歌名：《》里的第一段；没有就整个标题前 40 个字（链接点歌时只知道视频标题）"""
    m = re.search(r"[《「『]([^《》「」『』]{1,40})[》」』]", str(vt or ""))
    return m.group(1).strip() if m else str(vt or "")[:40]


def _yt_one(vid, title, artist, req, sec, out, log, cand=None):
    """下一个 YouTube 视频 → 80 秒闸门 → 找副歌剪好 → meta；不行抛 RuntimeError（中文）"""
    p, info = yt_download(vid)
    try:
        rs = real_seconds(p)
        if rs is not None and rs < MIN_REAL_SEC:
            raise RuntimeError(f"下下来只有 {rs:.0f} 秒（试听片段 不到 {MIN_REAL_SEC} 秒不用）")
        clip = _make_clip(p, sec, out, float(info.get("duration") or 0))
        if not clip:
            raise RuntimeError("剪不出来")
    finally:
        try:
            p.unlink()
        except OSError:
            pass
    st, length, how = clip
    cand = cand or {}
    vt = str(cand.get("title") or info.get("title") or "")
    up = str(cand.get("up") or info.get("channel") or info.get("uploader") or "")
    meta = {"title": title or _guess_title(vt), "artist": artist or "", "source": "YouTube", "src": "youtube", "id": vid,
            "video": vt[:120], "up": up, "start": round(st, 1), "how": how, "sec": round(length, 1), "cut_v": CUT_V,
            "pick_why": cand.get("why") or "", "clip": out.name, "t": time.time()}
    if cand.get("live"):
        meta["live_only"] = True
    log(f"[唱歌] 网上找「{req}」→ YouTube《{vt[:40]}》频道 {up}（{how} 从 {st:.0f} 秒开始 {length:.0f} 秒）"
        f"· 选它是因为：{cand.get('why') or '标题对得上'}")
    return meta


def _yt_fetch(title, artist, req, sec, out, cache, key, why, log, bad=(), studio=False, pre=None):
    """YouTube：yt-dlp 搜 → check_title 核对排好 → 前 BILI_TRY 个一个个试"""
    if pre is None:
        rej = []
        try:
            res = yt_search(f"{title} {artist}".strip())
        except RuntimeError as e:
            why.append(f"YouTube 搜不了：{e}")
            return None
        cands = yt_rank(res, title, artist, req, rej, studio, bad)
        log(f"[唱歌] YouTube 搜「{title} {artist}".rstrip() + f"」得到 {len(res)} 条 → 标题核对过的 {len(cands)} 条"
            + (("；核对掉的：" + "；".join(f"《{t[:26]}》{w}" for t, w in rej[:4])) if rej else ""))
        if not cands:
            why.append("YouTube：" + ("没有标题对得上的原唱视频" if res else "什么都没搜到"))
            return None
    else:
        cands = list(pre)
        if not cands:
            why.append("YouTube：没有标题对得上的原唱视频")
            return None
    note_ambiguity(ambiguity("YouTube", cands, title, artist), title, req, log)
    whys = []
    for cand in cands[:BILI_TRY]:
        try:
            meta = _yt_one(str(cand.get("id") or cand.get("bvid")), title, artist, req, sec, out, log, cand)
        except RuntimeError as e:
            whys.append(f"{cand.get('id')} {e}")
            continue
        if whys:
            log(f"[唱歌] YouTube 前面 {len(whys)} 个视频不能用（{whys[0][:70]}）→ 用的是第 {len(whys) + 1} 个")
        cache[key] = meta
        _save_cache(cache)
        return dict(meta, path=out)
    why.append("YouTube：" + ("；".join(whys[-2:]) or "音频下不下来"))
    return None


async def _bili_search_only(q, sessdata=""):
    """B站 只搜（拿不准时先看看 / 自检）"""
    import bili2
    c = bili2.Client(sessdata)
    try:
        try:
            await c.ready()
        except Exception as e:
            raise RuntimeError(f"连不上 / 被拦了（{e}）")
        return await bili_search(c, q)
    finally:
        try:
            await c.close()
        except Exception:
            pass


async def _bili_get(vid, page=1, sessdata=""):
    """B站 一个视频（BV… / av…）→ (音轨 bytes, 视频信息)；不行抛 RuntimeError"""
    import bili2
    c = bili2.Client(sessdata)
    vid = str(vid)
    params = {"aid": int(vid[2:])} if vid.lower().startswith("av") and vid[2:].isdigit() else {"bvid": vid}
    try:
        try:
            await c.ready()
        except Exception as e:
            raise RuntimeError(f"连不上 / 被拦了（{e}）")
        return await bili_audio(c, params, int(page or 1))
    finally:
        try:
            await c.close()
        except Exception:
            pass


async def _bili_one(vid, page, title, artist, req, sec, out, sessdata, log, cand=None):
    """B站 指定的一个视频（链接点歌 / 他选的）→ 80 秒闸门 → 找副歌剪好 → meta；不行抛 RuntimeError"""
    data, v = await _bili_get(vid, page, sessdata)
    v = v if isinstance(v, dict) else {}
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    t_ = TMP_DIR / f"bili_{re.sub(r'[^0-9A-Za-z]', '', str(vid))[:20]}.m4a"
    t_.write_bytes(data)
    try:
        rs = real_seconds(t_)
        if rs is not None and rs < MIN_REAL_SEC:
            raise RuntimeError(f"下下来只有 {rs:.0f} 秒（试听片段 不到 {MIN_REAL_SEC} 秒不用）")
        clip = _make_clip(t_, sec, out, float(v.get("duration") or 0))
        if not clip:
            raise RuntimeError("剪不出来")
    finally:
        try:
            t_.unlink()
        except OSError:
            pass
    st, length, how = clip
    cand = cand or {}
    vt = str(cand.get("title") or v.get("title") or "")
    up = str(cand.get("up") or (v.get("owner") or {}).get("name") or "")
    meta = {"title": title or _guess_title(vt), "artist": artist or "", "source": "B站", "src": "bili",
            "id": v.get("bvid") or str(vid), "video": vt[:120], "up": up, "start": round(st, 1), "how": how,
            "sec": round(length, 1), "cut_v": CUT_V, "pick_why": cand.get("why") or "", "clip": out.name, "t": time.time()}
    log(f"[唱歌] 「{req}」→ B站《{vt[:40]}》UP {up}（{how} 从 {st:.0f} 秒开始 {length:.0f} 秒）")
    return meta


async def _bili_fetch(title, artist, req, sec, out, sessdata, log, studio=False, bad=(), pre=None):
    """B站：搜（pre = 拿不准时已经搜过的 不再搜 11-01 v1.16.0）→ 核对排好 → 前 BILI_TRY 个一个个试 → 剪好 → meta；不行抛 RuntimeError"""
    import bili2
    c = bili2.Client(sessdata)
    tmp, x = None, None
    try:
        try:
            await c.ready()
        except Exception as e:
            raise RuntimeError(f"连不上 / 被拦了（{e}）")
        res = list(pre) if pre is not None else await bili_search(c, f"{title} {artist}".strip())
        rej = []
        cands = bili_rank(res, title, artist, req, rej, studio, bad)
        log(f"[唱歌] B站搜「{title} {artist}".rstrip() + f"」得到 {len(res)} 条 → 标题核对过的 {len(cands)} 条"
            + (("；核对掉的：" + "；".join(f"《{t[:26]}》{w}" for t, w in rej[:4])) if rej else ""))
        if not cands:
            raise RuntimeError("没有标题对得上的原唱视频" if res else "B站什么都没搜到")
        note_ambiguity(ambiguity("B站", cands, title, artist), title, req, log)
        whys = []
        for cand in cands[:BILI_TRY]:               # 第一个放不了（地区限制 / 拿不到音轨 / 下下来只有试听那么长）换下一个
            try:
                data, v = await bili_audio(c, {"bvid": cand["bvid"]})
            except RuntimeError as e:
                whys.append(f"{cand['bvid']} {e}")
                continue
            t_ = TMP_DIR / f"bili_{cand['bvid']}.m4a"
            t_.write_bytes(data)
            rs = real_seconds(t_)
            if rs is not None and rs < MIN_REAL_SEC:    # 10-29 第 8 件：下载下来量真实时长 过 80 秒闸门（防试听片段）
                whys.append(f"{cand['bvid']} 下下来只有 {rs:.0f} 秒（试听片段 不到 {MIN_REAL_SEC} 秒不用）")
                try:
                    t_.unlink()
                except OSError:
                    pass
                continue
            tmp, x = t_, cand
            break
        if x is None:
            raise RuntimeError("；".join(whys[-2:]) or "音轨下不下来")
        if whys:
            log(f"[唱歌] B站前面 {len(whys)} 个视频不能用（{whys[0][:70]}）→ 用的是第 {len(whys) + 1} 个")
    finally:
        try:
            await c.close()
        except Exception:
            pass
    try:
        if _full_max():                             # 唱整首（11-01 v1.16.5）：不剪 只去头尾静音
            fm = _make_full(tmp, out, _full_max())
            if not fm:
                raise RuntimeError("整首转不出来")
            st, length, how = fm
        else:
            pc = pick_clip(tmp, sec)                # 找副歌 + 对齐句子（10-31 v1.15.14）；不行退回最响的一段
            st, length, how = pc if pc else (loudest(tmp, sec), float(sec), "最响的一段")
            if not cut(tmp, st, length, out):
                raise RuntimeError("剪不出来")
    finally:
        try:
            tmp.unlink()
        except (OSError, AttributeError):
            pass
    # 歌手只写点歌的人说了的（UP 主不一定是唱的人：雨爱那个视频是 UP「JLRS-LeoFM」传的杨丞琳原唱）
    meta = {"title": title, "artist": artist or "", "source": "B站", "src": "bili", "id": x["bvid"],
            "video": x["title"][:120], "up": x["up"], "start": round(st, 1), "how": how, "sec": round(length, 1),
            "cut_v": CUT_V, "pick_why": x.get("why") or "", "clip": out.name, "t": time.time()}
    if x.get("live"):
        meta["live_only"] = True           # 要换声 可只找得到现场版：记下来（下次别又丢掉重找一遍 11-01 v1.15.18）
    log(f"[唱歌] 网上找「{req}」→ B站《{x['title'][:40]}》UP {x['up']}（{how} 从 {st:.0f} 秒开始 {length:.0f} 秒）"
        f"· 选它是因为：{x.get('why') or '标题对得上'}")
    return meta


# ───────────── 链接点歌（11-01 v1.16.0）─────────────
def _full(u):
    u = str(u or "").strip().rstrip("，。！？!?,.)）】]」>")
    return u if re.match(r"^https?://", u, re.I) else "https://" + u


def find_url(text):
    """一句话里的音乐 / 视频链接 → {"platform": "bili|youtube|netease|qq|kugou", "id", "url"}（短链 id 是空的、多一个 "short": True，
    要 fetch_url 跟一次跳转）；没有 None。不联网
    认：B站 BV号 / av号 / b23.tv；YouTube watch / youtu.be / music.youtube.com / shorts；网易云 song?id= / 163cn.tv；
    QQ 音乐 songDetail / songmid / songid / c6.y.qq.com 短链；酷狗 hash / mixsong / 别的 kugou.com 分享链接"""
    t = str(text or "")
    if not t.strip():
        return None
    m = re.search(r"(?:https?://)?(?:[\w-]+\.)?b23\.tv/[0-9A-Za-z]+", t, re.I)
    if m:
        return {"platform": "bili", "id": "", "url": _full(m.group(0)), "short": True}
    m = re.search(r"(?<![0-9A-Za-z])(BV1[0-9A-Za-z]{9})(?![0-9A-Za-z])", t)
    if m:
        p = re.search(r"[?&]p=(\d{1,3})", t)
        return {"platform": "bili", "id": m.group(1), "url": f"https://www.bilibili.com/video/{m.group(1)}",
                "page": int(p.group(1)) if p else 1}
    m = re.search(r"bilibili\.com/video/av(\d+)", t, re.I)
    if m:
        return {"platform": "bili", "id": "av" + m.group(1), "url": f"https://www.bilibili.com/video/av{m.group(1)}", "page": 1}
    m = re.search(r"(?:youtube\.com/(?:watch\?(?:\S*?&)?v=|shorts/|live/|embed/)|youtu\.be/)([0-9A-Za-z_-]{11})(?![0-9A-Za-z_-])", t, re.I)
    if m:
        return {"platform": "youtube", "id": m.group(1), "url": f"https://www.youtube.com/watch?v={m.group(1)}"}
    m = re.search(r"music\.163\.com/\S*?song(?:\?(?:\S*?&)?id=|/)(\d+)", t, re.I)
    if m:
        return {"platform": "netease", "id": m.group(1), "url": f"https://music.163.com/song?id={m.group(1)}"}
    m = re.search(r"(?:https?://)?163cn\.tv/[0-9A-Za-z]+", t, re.I)
    if m:
        return {"platform": "netease", "id": "", "url": _full(m.group(0)), "short": True}
    m = re.search(r"y\.qq\.com/\S*?(?:songDetail/|/song/|[?&]songmid=)([0-9A-Za-z]{10,20})", t)
    if m:
        return {"platform": "qq", "id": m.group(1), "url": f"https://y.qq.com/n/ryqq/songDetail/{m.group(1)}"}
    m = re.search(r"y\.qq\.com/\S*?[?&]songid=(\d+)", t)
    if m:
        return {"platform": "qq", "id": "id:" + m.group(1), "url": _full(re.search(r"\S*y\.qq\.com/\S*", t).group(0))}
    m = re.search(r"(?:https?://)?c\d*\.y\.qq\.com/base/fcgi-bin/u\?__=[0-9A-Za-z]+", t)
    if m:
        return {"platform": "qq", "id": "", "url": _full(m.group(0)), "short": True}
    m = re.search(r"kugou\.com/\S*?(?:hash=|play/info/)([0-9A-Fa-f]{32})", t, re.I)
    if m:
        a = re.search(r"album_id=(\d+)", t)
        return {"platform": "kugou", "id": m.group(1).upper(), "url": _full(re.search(r"\S*kugou\.com/\S*", t, re.I).group(0)),
                "album_id": a.group(1) if a else ""}
    m = re.search(r"(?:https?://)?(?:www\.)?kugou\.com/mixsong/([0-9A-Za-z]+)\.html", t, re.I)
    if m:
        return {"platform": "kugou", "id": "mix:" + m.group(1), "url": _full(m.group(0))}
    m = re.search(r"(?:https?://)?[\w.-]*kugou\.com/\S+", t, re.I)
    if m:
        return {"platform": "kugou", "id": "", "url": _full(m.group(0)), "short": True}
    return None


def _resolve(url):
    """短链接跟跳转 → (真链接, 页面文字) / (None, 原因)"""
    try:
        r = _http("GET", url, follow=True, timeout=10)
    except Exception as e:
        return None, f"打不开（{repr(e)[:50]}）"
    if r.status_code >= 400:
        return None, f"HTTP {r.status_code}"
    return (r.url or url), r.text[:300000]


def _scan_page(plat, page):
    """短链接跳到的页面里找这首歌（酷狗分享页的 hash 在页面 JS 里）"""
    if plat == "kugou":
        m = re.search(r"[\"']?hash[\"']?\s*[:=]\s*[\"']([0-9A-Fa-f]{32})[\"']", page or "")
        if m:
            a = re.search(r"[\"']?album_id[\"']?\s*[:=]\s*[\"']?(\d+)", page or "")
            return {"platform": "kugou", "id": m.group(1).upper(), "url": "", "album_id": a.group(1) if a else ""}
        return None
    u = find_url(page or "")
    return u if u and u.get("platform") == plat and not u.get("short") else None


def _js_str(s):
    try:
        return json.loads('"' + s + '"')
    except ValueError:
        return s


def _kg_mix(url):
    """酷狗 mixsong 页面 → 歌（hash / album_id / 歌名歌手在页面 JS 里）；读不到 None"""
    try:
        page = _http("GET", url, headers=KG_HEADERS, timeout=10).text
    except Exception:
        return None
    h = re.search(r"[\"']hash[\"']\s*:\s*[\"']([0-9A-Fa-f]{32})", page)
    if not h:
        return None
    a = re.search(r"[\"']album_id[\"']\s*:\s*[\"']?(\d+)", page)
    n = re.search(r"[\"'](?:song_name|audio_name|songname)[\"']\s*:\s*[\"']((?:[^\"'\\]|\\.)+)", page)
    s = re.search(r"[\"'](?:author_name|singername)[\"']\s*:\s*[\"']((?:[^\"'\\]|\\.)+)", page)
    return _kg_song({"hash": h.group(1), "album_id": a.group(1) if a else "",
                     "songname": _js_str(n.group(1)) if n else "", "singername": _js_str(s.group(1)) if s else "",
                     "filename": "" if n else "未知 - 未知"})


def _url_song(plat, u, sec, out, cache, key, why, log, sessdata, studio):
    """歌曲平台的链接：按 id 读这首 → 能放就下 → 放不了（没登录 / VIP / 地区）读出歌名歌手去别的来源按名字找"""
    nm = SRC_NAME[plat]
    idv = str(u.get("id") or "")
    s = None
    if plat == "netease":
        s = ne_detail(idv)
    elif plat == "qq":
        s = qq_detail(songid=idv[3:]) if idv.startswith("id:") else qq_detail(mid=idv)
    elif plat == "kugou":
        if idv.startswith("mix:"):
            s = _kg_mix(u.get("url") or "")
        else:
            s = kg_detail(idv) or {"id": idv, "name": "", "artists": [], "dur": 0, "x": {}}
            s.setdefault("x", {})
            if u.get("album_id") and not s["x"].get("album_id"):
                s["x"]["album_id"] = u["album_id"]
    if not s:
        why.append(f"{nm}读不到这首的信息（链接里的 id 不对 / 连不上）")
        return None
    if plat == "netease" or _logged(plat):
        got = _song_try(plat, [s], s.get("name") or "", u.get("url") or idv, sec, out, cache, key, why, log, tries=1)
        if got:
            return got
    else:
        why.append(f"{nm}没登录 放不了链接里这首")
    if not s.get("name"):
        return None
    req2 = f"{s['name']} - {s['artists'][0]}" if s.get("artists") else s["name"]
    log(f"[唱歌] 链接那首 {nm}《{s['name']}》放不了（{'；'.join(why[-1:])[:60]}）→ 按歌名歌手「{req2}」去别的来源找")
    got = fetch(req2, log, sec, sessdata, studio)
    if got.get("path"):
        got["pick_why"] = (str(got.get("pick_why") or "") + f"（链接是{nm}的《{s['name']}》那边放不了 按歌名歌手找的）").strip()
        return got
    why.append(str(got.get("fail") or "按歌名也没找到"))
    return None


def fetch_url(link, log=print, sec=12, sessdata="", studio=False):
    """链接点歌：B站 / YouTube 直接下这个视频；网易云 / QQ / 酷狗 先按 id 下，下不了就读出歌名歌手再按名字找
    → 成功同 fetch、多 "from_url": True；失败 {"fail": 原因}。link 可以是一句话，也可以是 find_url 给的 dict"""
    u = dict(link) if isinstance(link, dict) else find_url(link)
    if not u or u.get("platform") not in SRC_NAME:
        return {"fail": "认不出是哪个平台的歌 / 视频链接"}
    if not _ff():
        return {"fail": "没装 ffmpeg 剪不了"}
    plat = u["platform"]
    nm = SRC_NAME[plat]
    if u.get("short") or not u.get("id"):
        real, page = _resolve(u.get("url") or "")
        if not real:
            return {"fail": f"{nm}的短链接打不开（{page}）"}
        u2 = find_url(real)
        if not u2 or u2.get("short") or u2.get("platform") != plat or not u2.get("id"):
            u2 = _scan_page(plat, page) or u2
        if not u2 or u2.get("short") or u2.get("platform") != plat or not u2.get("id"):
            host = (urllib.parse.urlparse(real).hostname or "")[:40]
            return {"fail": f"{nm}的短链接跳到了 {host}、认不出是哪首"}
        u2["url"] = u2.get("url") or real
        log(f"[唱歌] 短链接 {u.get('url')} → {nm} {u2['id']}")
        u = u2
    key = ("full|" if _full_max() else "") + f"url|{plat}|{u['id']}"      # 唱整首（11-01 v1.16.5）：另记
    cache = _cache()
    hit = cache.get(key)
    if hit and (CLIP_DIR / hit.get("clip", "")).is_file():
        log(f"[唱歌] 链接 {nm} {u['id']} 以前下过 → 直接用《{hit.get('title')}》")
        return dict(hit, path=CLIP_DIR / hit["clip"], from_url=True)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    out = CLIP_DIR / f"{'full' if _full_max() else 'song'}_net_{hashlib.md5(key.encode('utf-8')).hexdigest()[:10]}.wav"
    why, got = [], None
    try:
        if plat in ("bili", "youtube"):
            if plat == "youtube":
                ok, w = _yt_ready()
                if not ok:
                    return {"fail": f"YouTube 用不了：{w}"}
                meta = _yt_one(u["id"], "", "", u.get("url") or u["id"], sec, out, log)
            else:
                meta = asyncio.run(_bili_one(u["id"], int(u.get("page") or 1), "", "", u.get("url") or u["id"],
                                             sec, out, sessdata, log))
            cache[key] = meta
            _save_cache(cache)
            got = dict(meta, path=out)
        else:
            got = _url_song(plat, u, sec, out, cache, key, why, log, sessdata, studio)
    except RuntimeError as e:
        why.append(f"{nm}：{e}")
    except Exception as e:
        why.append(f"{nm}出错：{repr(e)[:80]}")
    if got:
        got["from_url"] = True
        return got
    log(f"[唱歌] 链接点歌 {nm} {u.get('id')} 唱不了：" + "；".join(why[-3:]))
    return {"fail": "；".join(why[-3:]) or "链接里这首下不下来"}


# ───────────── 自检（11-01 v1.16.0：控制台「现在自检」/ 开机自检；主人电脑上一跑就知道哪个来源通）─────────────
SELFTEST_SONG = {"cn": "晴天 - 周杰伦", "en": "Shape of You - Ed Sheeran"}


def _selftest_audio(src, c, sessdata):
    """自检：拿第一个候选的音频存成临时文件（不剪不存）→ 路径；拿不到抛 RuntimeError"""
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    if src in SONG_SRC:
        a, w, ext = _song_audio(src, c)
        if not a:
            raise RuntimeError(f"《{c.get('name')}》{w}")
        p = TMP_DIR / f"selftest_{src}{ext or '.mp3'}"
        p.write_bytes(a)
        return p
    if src == "bili":
        data, _ = asyncio.run(_bili_get(c.get("bvid"), 1, sessdata))
        p = TMP_DIR / "selftest_bili.m4a"
        p.write_bytes(data)
        return p
    p, _ = yt_download(c.get("id"))
    return p


def selftest(log=print, sessdata=""):
    """每个来源搜一首常见歌（国内平台「晴天 - 周杰伦」、YouTube「Shape of You - Ed Sheeran」）、试拿音频量时长（不剪不存）
    → {source: {"enabled", "ok", "why", "found"(搜到几条), "sec"(音频秒数), "pick"(拿的哪个), "took"(秒)}}；每个来源一行 [唱歌] 自检 日志"""
    if not sessdata:
        try:
            sessdata = sys.modules["sing"].SESSDATA["v"]
        except Exception:
            sessdata = ""
    en = _cfg_sources()
    res = {}
    for src in DEFAULT_SOURCES:
        nm = SRC_NAME[src]
        r = res[src] = {"enabled": src in en, "ok": False, "why": "", "found": 0, "sec": None, "pick": "", "took": 0.0}
        try:
            ok, w, _ = _src_state(src)
        except Exception as e:
            ok, w = False, f"查不了（{repr(e)[:50]}）"
        if not ok:
            r["why"] = w
            log(f"[唱歌] 自检 {nm}：跳过（{w}）")
            continue
        req = SELFTEST_SONG["en" if src == "youtube" else "cn"]
        t, a = parse(req)
        t0 = time.time()
        p = None
        try:
            raw, cands = _search(src, t, a, req, sessdata, False, (), lambda *x, **k: None)
            r["found"] = len(raw or [])
            if not cands:
                raise RuntimeError(f"搜「{req}」得到 {len(raw or [])} 条 但对得上的一条都没有" if raw else f"搜「{req}」什么都没搜到")
            c = cands[0]
            r["pick"] = (f"{'、'.join(c.get('artists') or [])}《{c.get('name')}》" if src in SONG_SRC
                         else f"{nm}：{str(c.get('title') or '')[:40]}")
            p = _selftest_audio(src, c, sessdata)
            rs = real_seconds(p)
            r["sec"] = round(rs, 1) if rs is not None else None
            if rs is not None and rs < MIN_REAL_SEC:
                raise RuntimeError(f"{r['pick']} 下下来只有 {rs:.0f} 秒（试听片段：账号没权限 / VIP）")
            r["ok"] = True
        except RuntimeError as e:
            r["why"] = str(e)
        except Exception as e:
            r["why"] = f"出错（{repr(e)[:80]}）"
        finally:
            if p is not None:
                try:
                    Path(p).unlink()
                except OSError:
                    pass
        r["took"] = round(time.time() - t0, 1)
        if r["ok"]:
            log(f"[唱歌] 自检 {nm}：通（搜到 {r['found']} 条 → {r['pick']}" + (f" 音频 {r['sec']:.0f} 秒" if r["sec"] else "")
                + f"，用了 {r['took']}s）")
        else:
            log(f"[唱歌] 自检 {nm}：不通（{r['why']}）")
    return res
