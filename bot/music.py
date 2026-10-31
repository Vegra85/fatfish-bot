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
"""
import asyncio
import hashlib
import json
import re
import shutil
import subprocess
import time
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
NOT_ORIG = re.compile(r"伴奏|纯音乐|\binst\b|instrumental|翻唱|\bcover\b|翻自|\bdj\b|remix|改编|教学|吉他|钢琴|尤克(?:里){2}|口琴|小提琴|"
                      r"架子鼓|指弹|弹唱|合集|串烧|混剪|鬼畜|reaction|片段|\bcut\b|舞蹈|翻跳|练习|试唱|ai翻唱|ai版|ai cover|模仿|"
                      r"女声版|男声版|降调|升调|加速|倍速|慢速|铃声|教程|循环|1小时|一小时|10小时", re.I)
# 10-29 第 8 件：小明 的清单——排除 混剪 / MV / 字幕组 / 动画 / 合集 / 翻唱合集；优先 原唱 / 官方 / live / 歌词版
# （英文词前后不能紧挨着别的字母；中文紧挨着不算「挨着」：「官方MV」「动画MV」里的 MV 要抓到）
BAD_VIDEO = re.compile(r"混剪|(?<![a-z])a?mv(?![a-z])|字幕组|[中日韩英俄]{1,2}字幕|双语字幕|动画|动漫|番剧|合集|串烧|盘点|大全|歌单|二创|手书|reaction|解说|(?<![a-z])mad(?![a-z])", re.I)
PREFER_STRONG = re.compile(r"原唱|官方|official|(?<![a-z])live(?![a-z])|现场|演唱会|歌词版|动态歌词|(?<![a-z])lyrics?(?![a-z])", re.I)
PREFER = re.compile(r"高音质|无损|hi-?res|完整版|录音棚|audio|音乐(?!会|节|剧)", re.I)   # 「音乐会」不算（10-31 v1.15.15 小饭桌音乐会被当成「带音乐」）
LIVE = re.compile(r"(?<![a-z])live(?![a-z])|现场|演唱会|音乐会|小饭桌|小桌|tiny\s*desk|不插电|unplugged|直播|livehouse|音乐节", re.I)   # 换声开着时现场版往后排（观众声 / 混响 分不干净 换出来更糊）10-31 v1.15.14
CUT_V = 2                     # 剪法版本：10-31 v1.15.14 找副歌 + 对齐句子；记过的旧片段（最响的 12 秒）不再用 重新剪
MIN_REAL_SEC = 80             # 下载下来量到的真实时长不到这个数 = 试听片段 / 预览 → 不用
MAX_LEFTOVER_ASCII = 40       # 英文 / 数字多于这个数也算「别的内容」
MAX_LEFTOVER = 10             # 标题里除了歌名 / 歌手 / 装饰词 / 括号，剩下的汉字 / 外文字母多于这个数 = 歌名只是顺带提到（歌名在《》里的除外）
# 10-26 晚：电脑不在中国大陆时 网易云对海外 IP 的搜索只回一串加密的字（{"abroad": true, "result": "35b1…"}）
# 以前把这串字当歌单读 → AttributeError 整个唱歌出错、她只说了句「咳咳」。不去破解它：认出来就直接走 B站 6 小时后再试
ABROAD_HOLD = 6 * 3600
BILI_TRY = 3                  # B站 第一个视频放不了（地区限制/拿不到音轨）就换下一个 最多试这么多个
_NE = {"abroad_until": 0.0, "told": 0.0}


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


def _recheck_cached(hit, title, artist, raw_req):
    """记过的旧结果（data/song_online.json）按现在的规矩再核对一遍 → (ok, 理由)。只有 B站 记了视频标题的才能核对；网易云的、以前没记标题的照旧用"""
    vid = str(hit.get("video") or "")
    if hit.get("source") != "B站" or not vid:
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


# ───────────── 网易云 ─────────────
def ne_search(title, artist, client, log=print):
    if ne_abroad():
        return []
    q = f"{title} {artist}".strip()
    try:
        r = client.get("https://music.163.com/api/search/get/web",
                       params={"s": q, "type": 1, "offset": 0, "limit": 15}, headers=NE_HEADERS)
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
            log("[唱歌] 网易云把这台电脑当成海外 IP：搜索结果是加密的（abroad）用不了 → 现找先走 B站（6 小时后再试网易云）")
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
    out.sort(key=lambda x: (bool(re.search(r"live|现场|演唱会", str(x["name"]), re.I)),   # 录音室原版排前面
                            x["fee"] not in (0, 8, None)))                                  # 再是免费能播的（其余照网易云的顺序）
    return out


def ne_audio(sid, client):
    """官方外链：免费歌给 mp3；VIP / 只给大陆播的会跳到 404 → None"""
    try:
        r = client.get(f"https://music.163.com/song/media/outer/url?id={int(sid)}.mp3", headers=NE_HEADERS,
                       follow_redirects=True)
    except Exception:
        return None
    if r.status_code != 200 or "404" in str(r.url) or len(r.content) < 50_000:
        return None
    if "audio" not in r.headers.get("content-type", "") and not r.content[:3] in (b"ID3",) \
            and r.content[:2] not in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return None
    return r.content[:MAX_BYTES]


def ne_lyric(sid, client):
    try:
        j = client.get("https://music.163.com/api/song/lyric",
                       params={"id": int(sid), "lv": 1, "kv": 1, "tv": -1}, headers=NE_HEADERS).json()
        return ((j or {}).get("lrc") or {}).get("lyric") or ""
    except Exception:
        return ""


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


def bili_rank(results, title, artist, raw_req="", rejected=None, studio=False):
    """搜到的视频按「像原唱」排好（10-29 第 8 件：每一条都过 check_title 核对标题）：
    标题里有歌名；点了歌手的 标题或 UP 主里有歌手；不要 混剪 MV 字幕组 动画 合集 翻唱 伴奏 教学；歌名不能只是顺带提到；
    带 原唱 / 官方 / live / 歌词版 的排前面；视频本身不到 MIN_REAL_SEC 秒或超过 10 分钟的不要
    → 第一个放不了（地区限制、拿不到音轨）就试下一个（10-26 晚）
    rejected（list，可不给）：核对掉的每条追加 (标题, 原因)；返回的每一项多一个 "why"（为什么选它）"""
    out = []
    for i, x in enumerate(results or []):
        if not isinstance(x, dict):
            continue
        t = str(x.get("title") or "")
        ok, sc, why = check_title(t, x.get("up") or "", title, artist, raw_req)
        if ok and x.get("dur") and (x["dur"] < MIN_REAL_SEC or x["dur"] > 600):
            ok, why = False, f"视频时长 {x['dur']:.0f} 秒（要 {MIN_REAL_SEC}~600 秒）"
        if not ok:
            if rejected is not None:
                rejected.append((t, why))
            continue
        sc += len(str(x.get("play") or 0))                              # 播放量的位数（数量级）
        if studio and LIVE.search(t):                                   # 要换声：现场版往后排（10-31 v1.15.14）
            sc -= 8
            why += "（要换声 现场版往后排）"
        out.append((sc, i, dict(x, why=why, score=sc)))
    out.sort(key=lambda z: (-z[0], z[1]))          # 分高的在前；一样高的按 B站 给的顺序
    return [x for _, _, x in out]


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
    if src == "网易云":
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
        f"→ 以后会问一句要哪个（阶段 0 只记，照常用第一个）")
    try:
        import shadowlog
        shadowlog.write("ambiguity", {"what": "点歌", "title": str(title)[:40], "req": str(req or "")[:40], **amb})
    except Exception:
        pass


# ───────────── 找高潮 / 剪 ─────────────
def _ff():
    return shutil.which("ffmpeg")


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


# ───────────── 入口 ─────────────
def fetch(req, log=print, sec=12, sessdata="", studio=False):
    """点的歌 → {"path", "title", "artist", "source", "start"}；找不到 / 放不了 → {"fail": 原因}"""
    title, artist = parse(req)
    if not _norm(title):
        return {"fail": "没说是哪首"}
    if not _ff():
        return {"fail": "没装 ffmpeg 剪不了"}
    key = _key(title, artist)
    cache = _cache()
    hit = cache.get(key) or (cache.get(_key(title, "")) if not artist else None)
    if hit and hit.get("how") == "最响的一段" and int(hit.get("cut_v") or 1) < CUT_V:
        log(f"[唱歌] 「{req}」以前找过 但那段是老剪法（最响的 {sec} 秒 常常半句开始）→ 重新找、重新剪")
        hit = None
    if hit and (CLIP_DIR / hit.get("clip", "")).is_file():
        okc, whyc = _recheck_cached(hit, title, artist, req)
        if okc:
            log(f"[唱歌] 「{req}」以前找过：{hit.get('source')}《{hit.get('title')}》{hit.get('artist')}"
                + (f"（当时选的：{hit.get('pick_why')}）" if hit.get("pick_why") else ""))
            return dict(hit, path=CLIP_DIR / hit["clip"])
        log(f"[唱歌] 「{req}」以前找的 B站《{str(hit.get('video'))[:40]}》按现在的规矩核对不过（{whyc}）→ 丢掉重新找")
        for k in [k for k, v in cache.items() if v is hit]:
            cache.pop(k, None)
        _save_cache(cache)
    TMP_DIR.mkdir(parents=True, exist_ok=True)
    CLIP_DIR.mkdir(parents=True, exist_ok=True)
    tag = hashlib.md5(key.encode("utf-8")).hexdigest()[:10]
    out = CLIP_DIR / f"song_net_{tag}.wav"
    why = []
    # 1) 网易云（在海外用不了时 ne_search 直接返回空 不去要）
    try:
        got = _ne_fetch(title, artist, req, sec, out, cache, key, why, log)
    except Exception as e:                      # 网易云这边出什么错都别让唱歌整个挂掉 接着去 B站
        why.append(f"网易云出错：{repr(e)[:60]}")
        got = None
    if got:
        return got
    # 2) B站
    try:
        meta = asyncio.run(_bili_fetch(title, artist, req, sec, out, sessdata, log, studio))
    except RuntimeError as e:
        why.append(f"B站：{e}")
        meta = None
    except Exception as e:
        why.append(f"B站出错：{repr(e)[:80]}")
        meta = None
    if meta:
        cache[key] = meta
        _save_cache(cache)
        return dict(meta, path=out)
    log(f"[唱歌] 网上找「{req}」没找到能唱的：" + "；".join(why[-4:]))
    return {"fail": "；".join(why[-3:]) or "没找到"}


def _ne_fetch(title, artist, req, sec, out, cache, key, why, log):
    """网易云：歌名对上的免费原唱 → 剪好 → {…, "path"}；不行返回 None（原因追加进 why）"""
    with httpx.Client(timeout=15, headers={"User-Agent": UA}) as client:
        cands = ne_search(title, artist, client, log)
        note_ambiguity(ambiguity("网易云", cands, title, artist), title, req, log)
        if not cands:
            why.append("网易云在海外用不了（只给加密的结果）" if ne_abroad() else "网易云没有歌名对得上的")
        for s in cands[:4]:
            audio = ne_audio(s["id"], client)
            if not audio:
                why.append(f"网易云《{s['name']}》放不了（VIP 或者只给大陆播）")
                continue
            tmp = TMP_DIR / f"ne_{s['id']}.mp3"
            tmp.write_bytes(audio)
            try:
                rs = real_seconds(tmp)
                if rs is not None and rs < MIN_REAL_SEC:                  # 10-29 第 8 件：试听片段 / 预览
                    why.append(f"网易云《{s['name']}》下下来只有 {rs:.0f} 秒（试听片段 不到 {MIN_REAL_SEC} 秒不用）")
                    log(f"[唱歌] 网易云《{s['name']}》下下来只有 {rs:.0f} 秒 当试听片段 不用")
                    continue
                st = chorus_from_lrc(ne_lyric(s["id"], client), s["dur"])
                how, length = "歌词里的副歌", float(sec)
                if st is None:
                    pc = pick_clip(tmp, sec, s["dur"])
                    st, length, how = pc if pc else (loudest(tmp, sec, s["dur"]), float(sec), "最响的一段")
                if cut(tmp, st, length, out):
                    meta = {"title": s["name"], "artist": "、".join(s["artists"]), "source": "网易云", "sec": round(length, 1),
                            "cut_v": CUT_V, "id": s["id"], "start": round(st, 1), "how": how, "clip": out.name, "t": time.time()}
                    cache[key] = meta
                    _save_cache(cache)
                    log(f"[唱歌] 网上找「{req}」→ 网易云《{meta['title']}》{meta['artist']}（{how} 从 {st:.0f} 秒开始 {sec} 秒）")
                    return dict(meta, path=out)
                why.append("剪不出来")
            finally:
                try:
                    tmp.unlink()
                except OSError:
                    pass
    return None


async def _bili_fetch(title, artist, req, sec, out, sessdata, log, studio=False):
    import bili2
    c = bili2.Client(sessdata)
    tmp, x = None, None
    try:
        try:
            await c.ready()
        except Exception as e:
            raise RuntimeError(f"连不上 / 被拦了（{e}）")
        res = await bili_search(c, f"{title} {artist}".strip())
        rej = []
        cands = bili_rank(res, title, artist, req, rej, studio)
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
        pc = pick_clip(tmp, sec)                    # 找副歌 + 对齐句子（10-31 v1.15.14）；不行退回最响的一段
        st, length, how = pc if pc else (loudest(tmp, sec), float(sec), "最响的一段")
        if not cut(tmp, st, length, out):
            raise RuntimeError("剪不出来")
    finally:
        try:
            tmp.unlink()
        except (OSError, AttributeError):
            pass
    # 歌手只写点歌的人说了的（UP 主不一定是唱的人：雨爱那个视频是 UP「JLRS-LeoFM」传的杨丞琳原唱）
    meta = {"title": title, "artist": artist or "", "source": "B站", "id": x["bvid"],
            "video": x["title"][:120], "up": x["up"], "start": round(st, 1), "how": how, "sec": round(length, 1),
            "cut_v": CUT_V, "pick_why": x.get("why") or "", "clip": out.name, "t": time.time()}
    log(f"[唱歌] 网上找「{req}」→ B站《{x['title'][:40]}》UP {x['up']}（{how} 从 {st:.0f} 秒开始 {length:.0f} 秒）"
        f"· 选它是因为：{x.get('why') or '标题对得上'}")
    return meta
