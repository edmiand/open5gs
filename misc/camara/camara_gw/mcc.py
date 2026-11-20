"""MCC -> ISO 3166 alpha-2 (curated subset of ITU-T E.212 for the demo).

Unknown, test (001) and private/internal (999) MCCs map to [] (CAMARA: "If there is no
mapping of MCC to any country, then an empty array [] shall be returned").
Extend as needed; values are lab reference data, not authoritative.
"""

MCC_ISO: dict[int, list[str]] = {
    202: ["GR"], 204: ["NL"], 206: ["BE"], 208: ["FR"], 212: ["MC"], 214: ["ES"], 216: ["HU"],
    218: ["BA"], 219: ["HR"], 220: ["RS"], 222: ["IT"], 226: ["RO"], 228: ["CH"], 230: ["CZ"],
    231: ["SK"], 232: ["AT"], 234: ["GB"], 235: ["GB"], 238: ["DK"], 240: ["SE"], 242: ["NO"],
    244: ["FI"], 246: ["LT"], 247: ["LV"], 248: ["EE"], 250: ["RU"], 255: ["UA"], 257: ["BY"],
    259: ["MD"], 260: ["PL"], 262: ["DE"], 268: ["PT"], 270: ["LU"], 272: ["IE"], 274: ["IS"],
    278: ["MT"], 280: ["CY"], 282: ["GE"], 283: ["AM"], 284: ["BG"], 286: ["TR"], 293: ["SI"],
    294: ["MK"], 297: ["ME"],
    302: ["CA"], 310: ["US"], 311: ["US"], 312: ["US"], 313: ["US"], 314: ["US"], 315: ["US"],
    316: ["US"], 334: ["MX"],
    404: ["IN"], 405: ["IN"], 410: ["PK"], 413: ["LK"], 414: ["MM"], 415: ["LB"], 416: ["JO"],
    418: ["IQ"], 419: ["KW"], 420: ["SA"], 422: ["OM"], 424: ["AE"], 425: ["IL"], 426: ["BH"],
    427: ["QA"], 432: ["IR"], 434: ["UZ"], 437: ["KG"], 440: ["JP"], 441: ["JP"], 450: ["KR"],
    452: ["VN"], 454: ["HK"], 455: ["MO"], 456: ["KH"], 460: ["CN"], 466: ["TW"], 470: ["BD"],
    502: ["MY"], 505: ["AU"], 510: ["ID"], 515: ["PH"], 520: ["TH"], 525: ["SG"], 530: ["NZ"],
    602: ["EG"], 603: ["DZ"], 604: ["MA"], 605: ["TN"], 620: ["GH"], 621: ["NG"], 639: ["KE"],
    640: ["TZ"], 641: ["UG"], 655: ["ZA"],
    722: ["AR"], 724: ["BR"], 730: ["CL"], 732: ["CO"], 734: ["VE"], 716: ["PE"], 740: ["EC"],
    748: ["UY"],
}


def country_of(mcc: int) -> list[str]:
    return list(MCC_ISO.get(mcc, []))
