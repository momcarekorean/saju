# -*- coding: utf-8 -*-
"""일본 인기 육아용품 가격·재고 수집 (표준 라이브러리만 사용). 매일 1회 실행 → ../jp_prices.json

    python jp_fetch.py            # ../jp_prices.json 갱신(이전 값은 수집 실패 때 '마지막 확인값'으로 유지)
    python jp_fetch.py --out x.json --only stokke-chair,cybex-lemo

예의: 같은 사이트에 요청 사이 1.5초 이상, 하루 1회, 각 상품 페이지(robots.txt 허용 범위)만 읽는다.
수집처 = 스토케·부가부 일본 공식몰, 아카짱혼포 온라인. (가격 비교 사이트는 약관 때문에 자동 수집하지 않는다.)"""
import argparse, datetime, html, io, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jp_data as D  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "ja,en;q=0.8"}
KST = datetime.timezone(datetime.timedelta(hours=9))
DELAY = 1.6
_last = [0.0]


def get(url, tries=2):
    wait = DELAY - (time.time() - _last[0])
    if wait > 0:
        time.sleep(wait)
    err = None
    for i in range(tries):
        try:
            r = urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=30)
            raw = r.read()
            _last[0] = time.time()
            try:
                return raw.decode("utf-8")
            except UnicodeDecodeError:
                return raw.decode("cp932", "ignore")
        except Exception as e:  # noqa: BLE001
            err = e
            time.sleep(2)
    _last[0] = time.time()
    raise RuntimeError(str(err)[:120])


def avail_of(v):
    s = str(v or "").lower()
    if "instock" in s or "limitedavailability" in s or "onlineonly" in s:
        return "in"
    if "outofstock" in s or "soldout" in s or "discontinued" in s or "preorder" in s or "backorder" in s:
        return "out"
    return "unknown"


def price_of(v):
    try:
        p = float(str(v).replace(",", ""))
        return int(round(p)) if p > 0 else None
    except Exception:  # noqa: BLE001
        return None


def ld_blocks(h):
    out = []
    for m in re.finditer(r'<script[^>]*application/ld\+json[^>]*>(.*?)</script>', h, re.S):
        try:
            d = json.loads(m.group(1).strip())
        except Exception:  # noqa: BLE001
            continue
        out.extend(d if isinstance(d, list) else [d])
        for x in (d if isinstance(d, list) else [d]):
            if isinstance(x, dict) and isinstance(x.get("@graph"), list):
                out.extend(x["@graph"])
    return out


def offer_of(o):
    if isinstance(o, list):
        o = o[0] if o else {}
    return o if isinstance(o, dict) else {}


def ko_color(s):
    s = html.unescape(s)
    for k in sorted(D.COLOR_KO, key=len, reverse=True):
        s = re.sub(re.escape(k), D.COLOR_KO[k], s, flags=re.I)
    s = re.sub(r"\s*\(対応モデル[^)]*\)", "", s)
    s = re.sub(r"\s*CYBEX\(サイベックス\)\s*$", "", s)
    for a, b in (("年モデル", "년형"), ("プラス", " 플러스"), ("チャイルドシート", ""), ("[ISOFIX取付×回転式]", ""), ("[ISOFIX取付x回転式]", ""),
                 ("(セパレートモデル)", " 분리형 "), ("シローナ", "시로나 "), ("クラウド", "클라우드 "), ("ベース", "베이스 "), ("ブラック", "블랙"), ("ベージュ", "베이지")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip(" -")


def src_ld(s):
    h = get(s["url"])
    vs = []
    for x in ld_blocks(h):
        t = str(x.get("@type", "")).lower()
        if t == "productgroup" and x.get("hasVariant"):
            for v in x["hasVariant"]:
                o = offer_of(v.get("offers"))
                p = price_of(o.get("price"))
                if p:
                    nm = re.sub(r"^.*?(?:base|chassis),\s*", "", str(v.get("name", "")), flags=re.I) or str(v.get("color", ""))
                    nm = re.sub(r"\s*(sun canopy|fabrics).*$", "", nm, flags=re.I)
                    vs.append(dict(n=ko_color(nm), p=p, a=avail_of(o.get("availability"))))
            if vs:
                break
        if t in ("product", "productgroup") and not vs:
            o = offer_of(x.get("offers"))
            p = price_of(o.get("price"))
            if p:
                vs.append(dict(n="", p=p, a=avail_of(o.get("availability"))))
    if not vs:
        raise RuntimeError("가격을 못 읽음(ld+json 없음 또는 0원)")
    return vs


def parse_list(h):
    out = []
    for b in re.split(r'(?=<dl class="block-thumbnail-t--goods)', h)[1:]:
        a = re.search(r'href="(/shop/g/g\d+/?)"', b)
        n = re.search(r'goods-name">(.*?)</div>', b, re.S)
        p = re.search(r'price[^>]*>\s*([\d,]+)円', b)
        if a and n:
            out.append((a.group(1), html.unescape(re.sub(r"<[^>]+>", "", n.group(1))).strip(), price_of(p.group(1)) if p else None))
    return out


def src_akachan_list(s):
    rows = [r for r in parse_list(get(s["url"])) if re.search(s["name_re"], r[1])]
    vs = []
    for href, name, price in rows[:14]:
        try:
            h = get("https://shop.akachan.jp" + href)
            a, pr = "unknown", price
            for x in ld_blocks(h):
                if str(x.get("@type", "")).lower() == "product":
                    o = offer_of(x.get("offers"))
                    a = avail_of(o.get("availability"))
                    pr = price_of(o.get("price")) or price
                    break
        except Exception:  # noqa: BLE001
            a, pr = "unknown", price
        if pr:
            nm = re.sub(s.get("strip", "$^"), "", name) if s.get("strip") else name
            vs.append(dict(n=ko_color(nm), p=pr, a=a, u="https://shop.akachan.jp" + href))
    if not vs:
        raise RuntimeError("목록에서 상품을 못 찾음")
    return vs


RUN = {"ld": src_ld, "akachan_list": src_akachan_list}


def dedupe(vs):
    """같은 이름은 하나로(재고는 하나라도 있으면 있음). 액세서리(베이스)는 acc 표시."""
    order, seen = [], {}
    for v in vs:
        k = (v["n"], v["p"])
        if k not in seen:
            seen[k] = dict(v)
            order.append(k)
        elif v["a"] == "in" or (seen[k]["a"] == "unknown" and v["a"] != "unknown"):
            seen[k]["a"] = v["a"]
    out = [seen[k] for k in order]
    for v in out:
        if "베이스" in v["n"]:
            v["acc"] = True
    return out


def summarize(vs):
    main = [v for v in vs if not v.get("acc")]
    vs = main or vs
    prices = [v["p"] for v in vs]
    av = [v["a"] for v in vs]
    n_in = sum(1 for a in av if a == "in")
    known = [a for a in av if a != "unknown"]
    if not known:
        st = "unknown"
    elif n_in == len(av):
        st = "in"
    elif n_in == 0:
        st = "out"
    else:
        st = "part"
    return dict(price=min(prices), price_max=max(prices), avail=st, n_in=n_in, n_total=len(vs))


def fetch_rate():
    try:
        d = json.loads(get("https://open.er-api.com/v6/latest/JPY"))
        krw = d["rates"]["KRW"]
        t = datetime.datetime.strptime(d["time_last_update_utc"], "%a, %d %b %Y %H:%M:%S %z").astimezone(KST)
        return dict(jpy_krw=round(float(krw), 4), at=t.isoformat(timespec="minutes"), src="open.er-api.com(중간환율)")
    except Exception as e:  # noqa: BLE001
        print("환율 실패:", e)
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "jp_prices.json"))
    ap.add_argument("--only", default="")
    a = ap.parse_args()
    out = os.path.abspath(a.out)
    prev = {}
    if os.path.exists(out):
        try:
            prev = json.load(io.open(out, encoding="utf-8"))
        except Exception:  # noqa: BLE001
            prev = {}
    pitems = {x["id"]: x for x in prev.get("items", [])}
    only = set(filter(None, a.only.split(",")))
    now = datetime.datetime.now(KST)
    items, ok_n = [], 0
    for it in D.ITEMS:
        base = {k: it[k] for k in ("id", "brand", "name", "group", "desc")}
        base["kr"] = it.get("kr")
        base["kr_text"] = it.get("kr_text")
        if only and it["id"] not in only and it["id"] in pitems:
            items.append(pitems[it["id"]])
            continue
        vs, srcs = [], []
        for s in it["sources"]:
            rec = dict(label=s["label"], url=s["url"].replace("shop.akachan.jp/shop/goods/search.aspx", "shop.akachan.jp/shop/goods/search.aspx"))
            try:
                got = RUN[s["kind"]](s)
                vs.extend(got)
                rec["ok"] = True
            except Exception as e:  # noqa: BLE001
                rec["ok"] = False
                rec["error"] = str(e)[:100]
                print("실패", it["id"], s["label"], e)
            srcs.append(rec)
        vs = dedupe(vs)
        if vs:
            base.update(summarize(vs))
            base["variants"] = vs
            base["checked"] = now.isoformat(timespec="minutes")
            base["stale"] = False
            ok_n += 1
        else:
            old = pitems.get(it["id"])
            if old and old.get("price"):
                base.update({k: old.get(k) for k in ("price", "price_max", "avail", "n_in", "n_total", "variants", "checked")})
                base["stale"] = True
            else:
                fb = it["fallback"]
                base.update(price=fb["price"], price_max=fb["price"], avail="unknown", n_in=0, n_total=0, variants=[], checked=fb["date"] + "T00:00+09:00")
                base["stale"] = True
                base["fallback_note"] = fb.get("note", "자동 수집이 안 돼 마지막으로 확인한 값이에요")
        base["sources"] = srcs
        items.append(base)
    rate = fetch_rate() or prev.get("rate")
    data = dict(generated_at=now.isoformat(timespec="minutes"), rate=rate, items=items, stores=D.STORES, notes=D.NOTES, ok_items=sum(1 for x in items if not x.get("stale")), total_items=len(D.ITEMS))
    io.open(out, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=1))
    print("저장:", out, "| 성공 %d/%d" % (ok_n, len(D.ITEMS)), "| 환율", rate and rate["jpy_krw"])
    for x in items:
        print("%-20s %-8s %7s %s/%s %s" % (x["id"], x["avail"], x["price"], x.get("n_in"), x.get("n_total"), "(마지막 확인값)" if x["stale"] else ""))


if __name__ == "__main__":
    main()
