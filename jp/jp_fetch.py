# -*- coding: utf-8 -*-
"""일본 인기 육아용품 가격·한국 정발가·아카짱혼포 매장별 재고 수집 (표준 라이브러리만 사용). 매일 1회 실행 → ../jp_prices.json

    python jp_fetch.py            # ../jp_prices.json 갱신(수집 실패한 값은 '마지막 확인값'으로 유지)
    python jp_fetch.py --out x.json --only stokke-chair,cybex-lemo

예의: 같은 사이트에 요청 사이 1.6초 이상, 하루 1회, 각 상품 페이지(robots.txt 허용 범위)만 읽는다.
수집처 = 스토케·부가부 일본/한국 공식몰, 아카짱혼포 온라인(가격 + 매장별 참고 재고). AI·유료 API는 쓰지 않는다."""
import argparse, datetime, html, io, json, os, re, sys, time, urllib.parse, urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import jp_data as D  # noqa: E402

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36",
      "Accept-Language": "ja,ko;q=0.8,en;q=0.7"}
KST = datetime.timezone(datetime.timedelta(hours=9))
DELAY = 1.6
MAX_PAGES = 14
_last = [0.0]
MARK = {"〇": "in", "○": "in", "△": "low", "✕": "out", "×": "out", "－": "none", "-": "none", "―": "none"}
RANK = {"none": 0, "out": 1, "low": 2, "in": 3}


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
        items = d if isinstance(d, list) else [d]
        out.extend(items)
        for x in items:
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
                 ("(セパレートモデル)", " 분리형 "), ("シローナ", "시로나 "), ("クラウド", "클라우드 "), ("ベース", "베이스 ")):
        s = s.replace(a, b)
    return re.sub(r"\s+", " ", s).strip(" -")


def parse_stores(h):
    """아카짱혼포 상품 페이지에 들어 있는 점포별 참고 재고 → {점포번호: in/low/out/none}"""
    out = {}
    for sid, txt in re.findall(r'shop_name_"[^>]*href="https://stores\.akachan\.jp/(\d+)"[^>]*>[^<]+</a>\s*<div class="shop_stock_">([^<]*)</div>', h):
        t = html.unescape(txt).strip()
        if t:
            out[sid] = MARK.get(t[0], "none")
    return out


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


def akachan_page(href, name, price, strip):
    h = get("https://shop.akachan.jp" + href)
    a, pr = "unknown", price
    for x in ld_blocks(h):
        if str(x.get("@type", "")).lower() == "product":
            o = offer_of(x.get("offers"))
            a = avail_of(o.get("availability"))
            pr = price_of(o.get("price")) or price
            break
    nm = re.sub(strip, "", name) if strip else name
    return dict(n=ko_color(nm), p=pr, a=a, u="https://shop.akachan.jp" + href, stores=parse_stores(h))


def src_akachan_list(s):
    rows = [r for r in parse_list(get(s["url"])) if re.search(s["name_re"], r[1])]
    vs = []
    for href, name, price in rows[:MAX_PAGES]:
        try:
            v = akachan_page(href, name, price, s.get("strip"))
        except Exception:  # noqa: BLE001
            continue
        if v["p"]:
            vs.append(v)
    if not vs:
        raise RuntimeError("목록에서 상품을 못 찾음")
    return vs


def src_akachan_page(s):
    href = s["url"].replace("https://shop.akachan.jp", "")
    v = akachan_page(href, "", None, None)
    if not v["p"]:
        raise RuntimeError("가격을 못 읽음")
    v["n"] = ""
    return [v]


RUN = {"ld": src_ld, "akachan_list": src_akachan_list, "akachan_page": src_akachan_page}


def dedupe(vs):
    """같은 이름·가격은 하나로(점포별 재고는 가장 좋은 값). 베이스는 acc 표시."""
    order, seen = [], {}
    for v in vs:
        k = (v["n"], v["p"])
        if k not in seen:
            seen[k] = dict(v)
            order.append(k)
        else:
            if v["a"] == "in" or (seen[k]["a"] == "unknown" and v["a"] != "unknown"):
                seen[k]["a"] = v["a"]
            st = seen[k].setdefault("stores", {})
            for sid, m in (v.get("stores") or {}).items():
                if RANK.get(m, 0) > RANK.get(st.get(sid, "none"), 0):
                    st[sid] = m
    out = [seen[k] for k in order]
    for v in out:
        if "베이스" in v["n"]:
            v["acc"] = True
    return out


def summarize(vs):
    main = [v for v in vs if not v.get("acc")]
    vs = main or vs
    prices = [v["p"] for v in vs]
    return dict(price=min(prices), price_max=max(prices))


def build_store_stock(vs):
    out = {}
    for sid, (name, region) in D.STORE_STOCK.items():
        by, best = [], None
        for v in vs:
            m = (v.get("stores") or {}).get(str(sid))
            if m is None:
                continue
            by.append(dict(n=v["n"], m=m))
            if best is None or RANK[m] > RANK[best]:
                best = m
        if by:
            out[str(sid)] = dict(name=name, region=region, mark=best, by=by[:MAX_PAGES])
    return out


def src_godo(kr):
    """싸이벡스 한국 공식몰(고도몰) 상품 페이지의 판매가(set_goods_price)"""
    h = get(kr["url"])
    m = re.search(r'name="set_goods_price"\s+value="(\d+)"', h)
    p = price_of(m.group(1)) if m else None
    if not p:
        raise RuntimeError("판매가를 못 읽음")
    return [dict(n="", p=p, a="unknown")]


def fetch_kr(kr, prev):
    """한국 정발가: kind=ld 면 공식몰 상품 페이지에서 매일 읽고, 실패하면 이전 값(또는 fallback). static 은 사람이 넣은 값."""
    if not kr:
        return None
    now = datetime.datetime.now(KST).isoformat(timespec="minutes")
    base = dict(label=kr["label"], url=kr.get("url"))
    if kr["kind"] == "static":
        return dict(base, price=kr["price"], checked=kr["date"] + "T00:00+09:00", auto=False)
    try:
        vs = src_godo(kr) if kr["kind"] == "godo" else src_ld(kr)
        return dict(base, price=min(v["p"] for v in vs), checked=now, auto=True)
    except Exception as e:  # noqa: BLE001
        print("한국 정발가 실패", kr["label"], str(e)[:60])
        if prev and prev.get("price"):
            return dict(prev, auto=True, stale=True)
        return dict(base, price=kr["fallback"], checked=kr["date"] + "T00:00+09:00", auto=True, stale=True)


def fetch_rate():
    try:
        d = json.loads(get("https://open.er-api.com/v6/latest/JPY"))
        krw = d["rates"]["KRW"]
        t = datetime.datetime.strptime(d["time_last_update_utc"], "%a, %d %b %Y %H:%M:%S %z").astimezone(KST)
        return dict(jpy_krw=round(float(krw), 4), at=t.isoformat(timespec="minutes"), src="open.er-api.com(중간환율)")
    except Exception as e:  # noqa: BLE001
        print("환율 실패:", e)
        return None


def rec_page(url):
    """추천 소품 상품 페이지에서 가격(세금 포함)·사진·일본어 상품명을 읽는다."""
    h = get(url)
    for x in ld_blocks(h):
        if str(x.get("@type", "")).lower() == "product":
            o = offer_of(x.get("offers"))
            p = price_of(o.get("price"))
            im = x.get("image")
            im = im[0] if isinstance(im, list) and im else im
            if p:
                return dict(price=p, image=im if isinstance(im, str) and im.startswith("https://") else None, ja=str(x.get("name", ""))[:80])
    raise RuntimeError("가격을 못 읽음")


def fetch_recs(prev_recs, now):
    prev = {x["id"]: x for x in prev_recs or []}
    out, ok = [], 0
    for r in D.RECS:
        base = {k: r[k] for k in ("id", "brand", "name", "group", "desc", "why", "mentions", "url")}
        old = prev.get(r["id"]) or {}
        try:
            v = rec_page(r["url"])
            base.update(price=v["price"], image=v["image"] or old.get("image"), ja=v["ja"], checked=now.isoformat(timespec="minutes"), stale=False)
            ok += 1
        except Exception as e:  # noqa: BLE001
            print("추천 소품 실패", r["id"], str(e)[:60])
            if old.get("price"):
                base.update({k: old.get(k) for k in ("price", "image", "ja", "checked")})
            else:
                base.update(price=r["fallback"], image=None, ja="", checked="2026-10-19T00:00+09:00")
            base["stale"] = True
        out.append(base)
    return out, ok


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.join(HERE, "..", "jp_prices.json"))
    ap.add_argument("--only", default="")
    ap.add_argument("--min-ok", type=int, default=0, help="자동 확인된 품목이 이보다 적으면 파일을 쓰지 않고 실패(종료코드 1)")
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
        if only and it["id"] not in only and it["id"] in pitems:
            items.append(pitems[it["id"]])
            ok_n += 0 if pitems[it["id"]].get("stale") else 1
            continue
        old = pitems.get(it["id"]) or {}
        base = {k: it[k] for k in ("id", "brand", "name", "group", "desc")}
        base["kr_text"] = it.get("kr_text")
        base["jp_add"] = it.get("jp_add")
        vs, srcs = [], []
        for s in it["sources"]:
            rec = dict(label=s["label"], url=s["url"])
            try:
                vs.extend(RUN[s["kind"]](s))
                rec["ok"] = True
            except Exception as e:  # noqa: BLE001
                rec["ok"] = False
                rec["error"] = str(e)[:100]
                print("실패", it["id"], s["label"], e)
            srcs.append(rec)
        vs = dedupe(vs)
        sv = []
        for s in it.get("stock", []):
            try:
                sv.extend(RUN[s["kind"]](s))
            except Exception as e:  # noqa: BLE001
                print("재고 실패", it["id"], s["label"], str(e)[:60])
        sv = dedupe(sv) if sv else []
        if vs:
            base.update(summarize(vs))
            base["variants"] = vs
            base["checked"] = now.isoformat(timespec="minutes")
            base["stale"] = False
            ok_n += 1
        elif old.get("price"):
            base.update({k: old.get(k) for k in ("price", "price_max", "variants", "checked")})
            base["stale"] = True
        else:
            fb = it["fallback"]
            base.update(price=fb["price"], price_max=fb["price"], variants=[], checked=fb["date"] + "T00:00+09:00", stale=True,
                        fallback_note=fb.get("note", "자동 수집이 안 돼 마지막으로 확인한 값이에요"))
        # 아카짱혼포 매장별 재고: 가격 수집처가 아카짱혼포면 같은 페이지에서, 아니면 stock 수집처에서
        ss = build_store_stock(sv or [v for v in vs if v.get("stores")])
        if ss:
            base["store_stock"] = ss
            base["store_checked"] = now.isoformat(timespec="minutes")
        elif old.get("store_stock"):
            base["store_stock"] = old["store_stock"]
            base["store_checked"] = old.get("store_checked")
            base["store_stale"] = True
        base["kr"] = fetch_kr(it.get("kr"), old.get("kr"))
        for v in base.get("variants", []):
            v.pop("stores", None)
        base["sources"] = srcs
        items.append(base)
    if ok_n < a.min_ok:
        print("자동 확인 %d개 < 기준 %d개 — 파일을 쓰지 않아요(이전 값 유지)" % (ok_n, a.min_ok))
        sys.exit(1)
    recs, rec_ok = fetch_recs(prev.get("recs"), now)
    rate = fetch_rate() or prev.get("rate")
    data = dict(generated_at=now.isoformat(timespec="minutes"), rate=rate, items=items, stores=D.STORES, notes=D.NOTES,
                recs=recs, recs_groups=D.RECS_GROUPS, recs_basis=D.RECS_BASIS, recs_tips=D.RECS_TIPS,
                ok_items=sum(1 for x in items if not x.get("stale")), total_items=len(D.ITEMS))
    io.open(out, "w", encoding="utf-8").write(json.dumps(data, ensure_ascii=False, indent=1))
    print("저장:", out, "| 성공 %d/%d" % (data["ok_items"], len(D.ITEMS)), "| 추천 소품 %d/%d" % (rec_ok, len(recs)), "| 환율", rate and rate["jpy_krw"])
    for x in items:
        kr = x.get("kr")
        ss = x.get("store_stock") or {}
        print("%-20s ¥%7s %s KR %s | 점포재고 %s" % (x["id"], x["price"], "(마지막 확인값)" if x["stale"] else "", kr and kr["price"],
                                                 {k: v["mark"] for k, v in ss.items()}))


if __name__ == "__main__":
    main()
