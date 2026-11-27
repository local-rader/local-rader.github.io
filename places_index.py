"""청주·충주 동네 목록 → places-index.json (맨 위 동네 고르기에 두 도시를 함께 보여주려고)

  python places_index.py
data.js(청주)와 chungju/data.js(충주)의 기록에서 구 → 동네(동·읍·면) 목록을 뽑는다.
"""
import json
import os

HERE = os.path.dirname(os.path.abspath(__file__))


def permits(path):
    t = open(path, encoding="utf-8").read()
    return json.loads(t[t.index("{"):t.rindex("}") + 1])


def main():
    out = {}
    for key, d in (("cheongju", ""), ("chungju", "chungju")):
        P = permits(os.path.join(HERE, d, "data.js"))
        f, gus = P["fields"], P["meta"].get("gus", {})
        gi, pi = f.index("sg"), f.index("place")
        g = {}
        for r in P["rows"]:
            gu = gus.get(r[gi], "")
            a = (r[pi] or "").split(" ")[0]
            if gu and a:
                g.setdefault(gu, set()).add(a)
        town = lambda a: a.endswith(("읍", "면"))
        out[key] = {gu: sorted(v, key=lambda a: (town(a), a)) for gu, v in sorted(g.items())}
    with open(os.path.join(HERE, "places-index.json"), "w", encoding="utf-8") as fp:
        json.dump(out, fp, ensure_ascii=False, separators=(",", ":"))
    print({k: {g: len(v) for g, v in x.items()} for k, x in out.items()})


if __name__ == "__main__":
    main()
