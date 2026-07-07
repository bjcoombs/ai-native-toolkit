# golden-svg-repo — complexity-treemap golden fixture

Tiny, deterministic source tree with **known** cyclomatic complexity, consumed
by `tests/test_golden_svg_render.py` to lock the code-heatmap colour mapping
(`ccn -> hue`, `churn -> saturation`) and the SVG accessibility metadata.

The test copies these files into a temp dir, synthesizes a git history
(committing `hot.py` and `simple_active.py` several extra times to create
churn), then runs the **real** renderer (`scripts/complexity-treemap.py` via
`uv run --script`, no matplotlib stubbing) and parses the produced SVG.

## Files and their measured values (lizard)

| File                | file-aggregate CCN | git churn | role in the test                        |
|---------------------|-------------------:|----------:|-----------------------------------------|
| `hot.py`            | 21                 | high (5)  | high CCN + high churn -> vivid dark red |
| `complex_stable.py` | 21                 | low (1)   | high CCN + low churn -> desaturated red  |
| `simple_active.py`  | 2                  | high (5)  | low CCN + high churn -> pale, saturated |
| `simple_stable.py`  | 2                  | low (1)   | low CCN + low churn -> pale grey        |

`hot.py` and `complex_stable.py` are byte-identical in their scored function
(one function, 20 `if` decision points -> CCN 21), so their **base hue is
identical**; only their churn differs. That isolates the `churn -> saturation`
axis (compare the two) from the `ccn -> hue` axis (compare `hot.py` against the
equally-churned `simple_active.py`).

## Expected fill colours (golden values)

Deterministic from the OrRd colormap + the cap/blend maths (cap = max of data;
2-file complexity pair caps CCN at 21, churn at 5):

| File                | fill      | RGB             | chroma (max-min) |
|---------------------|-----------|-----------------|-----------------:|
| `hot.py`            | `#7f0000` | (127,   0,   0) | 127 (vivid)      |
| `complex_stable.py` | `#c0a7ab` | (192, 167, 171) | 25 (grey-blended)|
| `simple_active.py`  | `#fddbad` | (253, 219, 173) | 80               |
| `simple_stable.py`  | `#d9d3ce` | (217, 211, 206) | 11               |

If the colour-mapping logic changes, these fills move and the golden test
fails - update this table and the test together, deliberately.
