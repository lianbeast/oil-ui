"""Exercise the zero-dependency screenshot CLI against a real local browser."""

import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "shoot.mjs"
NODE = shutil.which("node")


def find_browser():
    candidates = [os.environ.get("CHROME_PATH")]
    if sys.platform == "darwin":
        candidates += [
            "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
            "/Applications/Chromium.app/Contents/MacOS/Chromium",
            "/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge",
        ]
    elif sys.platform == "win32":
        for variable in ("PROGRAMFILES", "PROGRAMFILES(X86)"):
            root = os.environ.get(variable)
            if root:
                candidates += [
                    str(Path(root) / "Google/Chrome/Application/chrome.exe"),
                    str(Path(root) / "Microsoft/Edge/Application/msedge.exe"),
                ]
    candidates += [shutil.which(name) for name in (
        "google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
    )]
    return next((path for path in candidates if path and Path(path).is_file()), None)


@unittest.skipUnless(NODE, "Node is not installed")
class ShootCLITests(unittest.TestCase):
    def test_help(self):
        result = subprocess.run([NODE, str(SCRIPT), "--help"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("用法", result.stdout)
        self.assertIn("type 在当前光标处插入文字", result.stdout)
        self.assertIn("fill 先清空原内容", result.stdout)
        self.assertNotIn("fill=type", result.stdout)
        self.assertIn("不进入 iframe 或 Shadow DOM", result.stdout)
        self.assertIn("中心点遮挡和禁用状态", result.stdout)

    def test_missing_target(self):
        result = subprocess.run([NODE, str(SCRIPT)], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertNotEqual(result.returncode, 0)

    def test_unknown_option(self):
        help_result = subprocess.run([NODE, str(SCRIPT), "--help"], cwd=ROOT,
                                     capture_output=True, text=True, timeout=10)
        self.assertEqual(help_result.returncode, 0, help_result.stderr)
        options = [line.split()[0] for line in help_result.stdout.splitlines()
                   if line.startswith("  --")]
        for args in (("--xxx",), ("--xxx", "value")):
            with self.subTest(args=args):
                result = subprocess.run([NODE, str(SCRIPT), *args], cwd=ROOT,
                                        capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                self.assertIn("不认识的选项 --xxx", result.stderr)
                self.assertIn("是不是想写", result.stderr)
                self.assertIn("可用选项：" + " ".join(options), result.stderr)
                self.assertEqual(result.stdout, "")

    def test_missing_option_value(self):
        for option in ("--out", "--size", "--states", "--param", "--zoom", "--steps", "--hold", "--wait", "--compare"):
            for following in ((), ("--force",)):
                with self.subTest(option=option, following=following):
                    result = subprocess.run([NODE, str(SCRIPT), option, *following], cwd=ROOT,
                                            capture_output=True, text=True, timeout=10)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stderr, f"shoot：{option} 需要一个值\n")

    def test_force_is_ignored(self):
        result = subprocess.run([NODE, str(SCRIPT), "--force"], cwd=ROOT,
                                capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 1)
        self.assertEqual(result.stderr, "shoot：缺少页面地址或文件。\n")

    def dry(self, *args, ok=True):
        result = subprocess.run([NODE, str(SCRIPT), "page.html", "--dry-run", *args],
                                cwd=ROOT, capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0 if ok else 1, result.stdout + result.stderr)
        return json.loads(result.stdout) if ok else result.stderr

    def test_dry_run_corpus_regressions(self):
        # Twenty corpus patterns; project-specific classes, labels and text are neutralized.
        cases = [
            ('click "[data-thread=sample]"', "click", "[data-thread=sample]"),
            ('click [data-id="sample"]', "click", '[data-id="sample"]'),
            ('click \'.cause [data-latest-release]\'', "click", '.cause [data-latest-release]'),
            ('click .nav button[data-view=sample]', "click", '.nav button[data-view=sample]'),
            ('click [aria-label="Send money"]', "click", '[aria-label="Send money"]'),
            ('click "[aria-label=\\"Send money\\"]"', "click", '[aria-label="Send money"]'),
            ('click \'[aria-label="Start Sample task"]\'', "click", '[aria-label="Start Sample task"]'),
            ('click ".nav button:nth-child(3)"', "click", '.nav button:nth-child(3)'),
            ('hover "[data-message=sample] .reaction"', "hover", '[data-message=sample] .reaction'),
            ('drag "[data-tile=\\"1\\"] .tile-paper" 240 0', "drag", '[data-tile="1"] .tile-paper'),
            ('drag [data-id="sample"] 214 -350', "drag", '[data-id="sample"]'),
            ('drag ".sample-line:nth-child(2)" 45 0', "drag", '.sample-line:nth-child(2)'),
            ('type #message-input 示例内容', "type", '#message-input'),
            ('type [aria-label="Message box"] 你好', "type", '[aria-label="Message box"]'),
            ('type "#search" Sample', "type", '#search'),
            ('click text=More', "click", 'More'),
            ('click button:has-text("Undo")', "click", 'Undo'),
            ('wait450', "wait", None),
            ('key ControlOrMeta+A', "key", None),
            ('key Meta+r', "key", None),
        ]
        for raw, action, selector in cases:
            with self.subTest(raw=raw):
                step = self.dry("--steps", raw)["groups"][0]["steps"][0]
                self.assertEqual(step["action"], action)
                if selector is not None:
                    self.assertEqual(step["selector"]["value"], selector)

    def test_dry_run_balanced_steps_and_parameters(self):
        parsed = self.dry("--steps", 'open: click [data-label="a;b"]; click :is([data-label="a;b"]); '
                          'type ".form input" "hello; world"; select #choice "Second item"; '
                          'wait450; wait 450ms; wait 0.5s; key Shift+Tab',
                          "--steps", 'click :has-text(\'Undo; action\')', "--mark", '1=text="a;b"')
        steps = parsed["groups"][0]["steps"]
        self.assertEqual(parsed["groups"][0]["name"], "open")
        self.assertEqual(len(steps), 8)
        self.assertEqual(steps[2]["params"], {"text": "hello; world"})
        self.assertEqual(steps[3]["params"], {"value": "Second item"})
        self.assertEqual([s["params"]["ms"] for s in steps[4:7]], [450, 450, 500])
        self.assertEqual(parsed["groups"][1]["name"], None)
        self.assertEqual(parsed["marks"][0]["selector"]["kind"], "text")
        for raw, expected in [('type #input "text [without a closing bracket"', 'text [without a closing bracket'),
                              ("type #input don't stop", "don't stop"),
                              ('type "body [aria-label="Message box"]" "hello world"', 'hello world')]:
            self.assertEqual(self.dry("--steps", raw)["groups"][0]["steps"][0]["params"]["text"], expected)
        for raw, action in [('doubleclick .a', 'dblclick'), ('press Control+A', 'key'),
                            ('fill #input "hello"', 'fill'), ('sleep 0.5s', 'wait'), ('waitfor text=Ready', 'waitfor')]:
            self.assertEqual(self.dry("--steps=" + raw)["groups"][0]["steps"][0]["action"], action)

    def test_fill_parses_as_replacement_action(self):
        for raw, kind, value, text in [
            ('fill ".form input" "hello world"', 'css', '.form input', 'hello world'),
            ('fill [aria-label="Message box"] ""', 'css', '[aria-label="Message box"]', ''),
            ('fill text="Old message" new', 'text', 'Old message', 'new'),
            ("fill textarea:has-text('Old message') new", 'has-text', 'Old message', 'new'),
        ]:
            with self.subTest(raw=raw):
                step = self.dry('--steps', raw)['groups'][0]['steps'][0]
                self.assertEqual(step['action'], 'fill')
                self.assertEqual(step['selector']['kind'], kind)
                self.assertEqual(step['selector']['value'], value)
                self.assertEqual(step['params'], {'text': text})
        self.assertIn('例如 fill #field "hello world"', self.dry('--steps', 'fill #field', ok=False))

    def test_dry_run_does_not_launch_or_write_and_shares_errors(self):
        with tempfile.TemporaryDirectory(prefix="oil-shoot-dry-") as folder:
            output = Path(folder) / "never-created"
            env = dict(os.environ, CHROME_PATH=str(Path(folder) / "no-browser"))
            args = [NODE, str(SCRIPT), "missing-page.html", "--out", str(output)]
            result = subprocess.run([*args, "--dry-run", "--steps", "click .nav button"],
                                    cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(json.loads(result.stdout)["groups"][0]["steps"][0]["selector"]["kind"], "css")
            self.assertFalse(output.exists())
            errors = []
            for extra in ([], ["--dry-run"]):
                result = subprocess.run([*args, *extra, "--steps", "drag .tile 20 nope"],
                                        cwd=ROOT, env=env, capture_output=True, text=True, timeout=10)
                self.assertEqual(result.returncode, 1)
                errors.append(result.stderr)
            self.assertEqual(errors[0], errors[1])

    def test_option_forms_aliases_and_errors(self):
        parsed = self.dry("--size=desktop,phone,mobile,800x600", "--zoom", "1,2", "--query=a=1&b=2", "--states", "idle,done")
        self.assertEqual(parsed["sizes"], [{"w": 1440, "h": 900}, {"w": 390, "h": 844}, {"w": 800, "h": 600}])
        self.assertEqual(parsed["zooms"], [1, 2])
        self.assertEqual(self.dry("--size", "mobile")["sizes"], [{"w": 390, "h": 844}])
        self.assertTrue(any("尺寸 已去重" in n for n in parsed["notices"]))
        self.assertEqual(self.dry()["sizes"], [{"w": 390, "h": 844}])
        for flag in ("--full", "--mask", "--sheet", "--record", "--motion", "--entry", "--evidence"):
            with self.subTest(flag=flag):
                error = self.dry(flag, "extra", ok=False)
                self.assertIn("page.html 和 extra", error)
                self.assertIn(flag + " 不带参数", error)
        for args, expected in [
            (("--hold1000",), "--hold 1000"), (("--zoon", "2"), "--zoom"),
            (("--motion", ".respond"), '--motion 不带参数'),
            (("other.html",), "page.html 和 other.html"),
            (("--full", "extra"), "page.html 和 extra"),
            (("--record=true",), "不带参数"), (("--zoom=0",), "--zoom 1,2"),
            (("--size=0x900",), "--size desktop"), (("--wait=abc",), "--wait 1000"),
            (("--steps", "clik .open"), "是不是想写 click"),
            (("--steps", "drag .a 1 nope"), "drag .tiles .paper 240 0"),
            (("--steps", "wait nope"), "wait 450ms"),
            (("--steps", "click [aria-label=\"x]"), "没有配对"),
            (("--steps", "key Invalid+A"), "ControlOrMeta+A"),
            (("--steps", "same: click .a", "--steps", "same: click .b"), "名字重复"),
        ]:
            with self.subTest(args=args):
                error = self.dry(*args, ok=False)
                self.assertIn(expected, error)
                if args == ("--hold1000",):
                    self.assertEqual(error.count("--hold 1000"), 1)
                    self.assertNotIn("例如", error)



class ShootBrowserTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not NODE:
            raise unittest.SkipTest("Node 22+ is not installed")
        cls.browser = find_browser()
        if not cls.browser:
            raise unittest.SkipTest("Chrome, Chromium or Edge is not installed")

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="oil-shoot-test-")
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        self.page = self.folder / "sample.html"
        self.page.write_text('''<!doctype html><html><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,">
<style>
body { margin: 0; padding: 24px; background: #fde68a; font: 20px sans-serif; }
body[data-state="b"] { background: #bfdbfe; }
button { padding: 16px; }
</style></head><body><h1 id="state"></h1><button id="go">切换</button>
<script>
const state = new URLSearchParams(location.search).get('state') || 'a';
function show(value) {
  document.body.dataset.state = value;
  document.querySelector('#state').textContent = value;
}
show(state);
document.querySelector('#go').onclick = () => show(document.body.dataset.state === 'a' ? 'b' : 'a');
</script></body></html>''', encoding="utf-8")
        self.env = dict(os.environ, CHROME_PATH=self.browser)
        self.profile_root = self.folder / "profiles"
        self.profile_root.mkdir()
        self.env.update(TMPDIR=str(self.profile_root), TMP=str(self.profile_root), TEMP=str(self.profile_root))

    def shoot(self, output, *args):
        result = subprocess.run([NODE, str(SCRIPT), str(self.page), "--out", str(output), *args],
                                cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=90)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def assert_artifacts(self, output, names):
        for name in names:
            with self.subTest(file=name):
                artifact = output / name
                self.assertTrue(artifact.is_file(), name)
                self.assertGreater(artifact.stat().st_size, 0, name)
                if artifact.suffix == ".png":
                    self.assertTrue(artifact.read_bytes().startswith(b"\x89PNG\r\n\x1a\n"), name)
                elif artifact.suffix == ".jpg":
                    self.assertTrue(artifact.read_bytes().startswith(b"\xff\xd8"), name)

    def test_states_masks_and_sheets(self):
        output = self.folder / "shots"
        self.shoot(output, "--states", "a,b", "--mask", "--sheet")
        self.assert_artifacts(output, (
            "a.png", "b.png", "a-masked.png", "b-masked.png",
            "sheet.png", "sheet-masked.png", "report.json",
        ))
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["state"] for entry in report], ["a", "b"])
        self.assertEqual([entry["issues"] for entry in report], [[], []])
        self.assertEqual([entry["lint"] for entry in report], [[], []])
        self.assertNotEqual((output / "a.png").read_bytes(), (output / "b.png").read_bytes())
        self.assertNotEqual((output / "a.png").read_bytes(), (output / "a-masked.png").read_bytes())
        self.assertEqual(list(self.profile_root.glob("oil-shoot-*")), [], "Temporary browser profiles leaked")

    def test_mark_draws_numbered_boxes_without_touching_plain_shot(self):
        output = self.folder / "marked"
        result = self.shoot(output, "--mark", "#go; h1")
        self.assert_artifacts(output, ("page.png", "page-marked.png"))
        self.assertIn("page-marked.png", result.stdout)
        self.assertNotEqual((output / "page.png").read_bytes(), (output / "page-marked.png").read_bytes())
        plain = self.folder / "plain"
        self.shoot(plain)
        self.assertEqual((output / "page.png").read_bytes(), (plain / "page.png").read_bytes(),
                         "Marks must not leak into the plain screenshot")

    def test_mark_accepts_explicit_numbers(self):
        numbered, by_order, reversed_order = self.folder / "numbered", self.folder / "by-order", self.folder / "reversed"
        self.shoot(numbered, "--mark", "2=#go; 1=h1")
        self.shoot(by_order, "--mark", "h1; #go")
        self.shoot(reversed_order, "--mark", "#go; h1")
        self.assertEqual((numbered / "page-marked.png").read_bytes(), (by_order / "page-marked.png").read_bytes())
        self.assertNotEqual((numbered / "page-marked.png").read_bytes(), (reversed_order / "page-marked.png").read_bytes(),
                            "Explicit numbers must decide the labels, not the order")

    def test_mark_reports_missing_elements(self):
        output = self.folder / "missing"
        result = subprocess.run([NODE, str(SCRIPT), str(self.page), "--out", str(output), "--mark", "#go; .nope"],
                                cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=90)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn(".nope", result.stderr)

    def test_force_overwrites_existing_output(self):
        output = self.folder / "shots"
        output.mkdir()
        (output / "page.png").write_bytes(b"old screenshot")
        self.shoot(output, "--force")
        self.assert_artifacts(output, ("page.png", "report.json"))

    def test_preview_denies_sibling_paths_and_escaping_symlinks(self):
        outside = Path(str(self.folder) + '-private')
        outside.mkdir()
        self.addCleanup(shutil.rmtree, outside)
        (outside / 'secret.txt').write_text('test-only-secret', encoding='utf-8')
        (self.folder / 'leak.txt').symlink_to(outside / 'secret.txt')
        (self.folder / 'nested').mkdir()
        (self.folder / 'nested/okay.txt').write_text('allowed-resource', encoding='utf-8')
        (self.folder / '.env.production.local').write_text('synthetic-secret', encoding='utf-8')
        (self.folder / '.git').mkdir()
        (self.folder / '.git/config').write_text('synthetic-git-config', encoding='utf-8')
        (self.folder / 'credentials.json').write_text('{"token":"synthetic"}', encoding='utf-8')
        (self.folder / 'server.pem').write_text('synthetic-private-key', encoding='utf-8')
        (self.folder / 'env.txt').symlink_to(self.folder / '.env.production.local')
        (self.folder / 'nested/hidden').symlink_to(self.folder / '.git', target_is_directory=True)
        (self.folder / 'nested/module.mjs').write_text('export const value = "module-works";', encoding='utf-8')
        blocked = ['/..%2f' + outside.name + '%2fsecret.txt', '/leak.txt', '/%E0%A4%A',
                   '/.env.production.local', '/%2eenv.production.local', '/.git/config',
                   '/credentials.json', '/server.pem', '/env.txt', '/nested/hidden/config']
        probe = '''<script>(async () => {
          for (const path of PATHS) {
            const response = await fetch(path);
            if (response.status !== 404) console.error('SECURITY_LEAK:' + path);
          }
          const allowed = await fetch('/nested/okay.txt');
          if (await allowed.text() !== 'allowed-resource') console.error('LEGIT_RESOURCE_BLOCKED');
          const module = await import('/nested/module.mjs');
          if (module.value !== 'module-works') console.error('LEGIT_RESOURCE_BLOCKED');
          console.error('AUDIT_FINISHED');
        })().catch(() => console.error('AUDIT_FAILED'));</script>'''.replace('PATHS', json.dumps(blocked))
        self.page.write_text(self.page.read_text().replace('</body>', probe + '</body>'), encoding='utf-8')
        output = self.folder / 'boundary-shots'
        self.shoot(output, '--wait', '1000')
        report = json.loads((output / 'report.json').read_text())
        issues = '\n'.join(report[0]['issues'])
        self.assertIn('AUDIT_FINISHED', issues)
        for marker in ('SECURITY_LEAK', 'LEGIT_RESOURCE_BLOCKED', 'AUDIT_FAILED'):
            self.assertNotIn(marker, issues)

    def test_state_labels_do_not_become_output_paths(self):
        output = self.folder / 'safe-states'
        states = ['../escaped', '<label & "quoted">']
        self.shoot(output, '--states', ','.join(states), '--mask', '--sheet')
        report = json.loads((output / 'report.json').read_text())
        self.assertEqual([entry['state'] for entry in report], states)
        for entry in report:
            self.assertRegex(entry['file'], r'^state-\d+-[0-9a-f]{12}\.png$')
            self.assertTrue((output / entry['file']).is_file())
        self.assertFalse((self.folder / 'escaped.png').exists())
        self.assert_artifacts(output, ('sheet.png', 'sheet-masked.png'))

    def test_type_accepts_selectors_with_quotes(self):
        self.page.write_text(self.page.read_text().replace('</body>', '<input data-x="value"></body>'), encoding='utf-8')
        output = self.folder / 'quoted-selector'
        self.shoot(output, '--steps', '''type 'input[data-x="value"]' hello''')
        report = json.loads((output / 'report.json').read_text())
        self.assertEqual(report[0]['issues'], [])

    def test_record_steps(self):
        output = self.folder / "record's output"
        result = self.shoot(output, "--record", "--steps", "click #go; wait 300", "--hold", "300")
        self.assert_artifacts(output, ("motion-start.jpg", "motion-mid.jpg", "motion-end.jpg"))
        self.assertNotEqual((output / "motion-start.jpg").read_bytes(), (output / "motion-end.jpg").read_bytes())
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual([entry["issues"] for entry in report], [[]])
        self.assertEqual(list(self.profile_root.glob("oil-shoot-*")), [], "Temporary browser profiles leaked")
        if shutil.which("ffmpeg"):
            self.assertTrue((output / "record.mp4").is_file(), result.stdout + result.stderr)
            self.assert_artifacts(output, ("record.mp4",))

    def test_mask_preserves_current_color_icons(self):
        self.page.write_text('''<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,"></head><body>
<svg width="80" height="80" viewBox="0 0 80 80" style="color:#16a34a">
<circle cx="40" cy="40" r="32" fill="currentColor" /></svg>
</body></html>''', encoding="utf-8")
        output = self.folder / "icons"
        self.shoot(output, "--mask")
        self.assertEqual((output / "page.png").read_bytes(), (output / "page-masked.png").read_bytes(),
                         "Masking text must preserve icons using currentColor")

    def test_record_preserves_final_hold(self):
        if not shutil.which("ffmpeg") or not shutil.which("ffprobe"):
            self.skipTest("ffmpeg and ffprobe are required to check recording duration")
        output = self.folder / "hold"
        self.shoot(output, "--record", "--steps", "click #go; wait 300", "--hold", "2000")
        result = subprocess.run([
            "ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "json",
            str(output / "record.mp4"),
        ], capture_output=True, text=True, timeout=10)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertGreaterEqual(float(json.loads(result.stdout)["format"]["duration"]), 2.0)

    def test_motion_probe(self):
        self.page.write_text('''<!doctype html><html><head>
<meta name="viewport" content="width=device-width, initial-scale=1">
<link rel="icon" href="data:,"><style>
body{margin:0}.hero{height:1800px}.stage{position:sticky;top:0;height:800px;overflow:hidden}
#figure,#word{position:absolute;left:300px;width:400px;height:300px;background:#888}#word{top:400px;background:#444}
@keyframes rise{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}
h1{animation:rise .5s ease-out both}#go{transition:transform .3s}#go.on{transform:translateX(40px)}
.reveal{height:600px;opacity:0;transition:opacity .3s}.reveal.in{opacity:1}
</style></head><body><section class="hero"><div class="stage"><div id="figure"></div><div id="word"></div></div></section>
<h1>Hi</h1><button id="go" onclick="this.classList.add('on')">Go</button>
<div style="height:1200px"></div><div class="reveal">Later</div>
<script>addEventListener('scroll',()=>{const p=Math.min(scrollY/1000,1);figure.style.transform=`scale(${1-p*.2})`;word.style.transform=`scale(${1+p*.25})`});
new IntersectionObserver(e=>e.forEach(x=>x.isIntersecting&&x.target.classList.add('in'))).observe(document.querySelector('.reveal'))</script>
</body></html>''', encoding="utf-8")
        output = self.folder / "motion"
        self.shoot(output, "--motion", "--size", "1280x800", "--steps", "click #go")
        probe = json.loads((output / "report.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(probe["issues"], [])
        self.assertTrue(all(probe["motion"][k]["elements"] for k in ("load", "steps", "hero", "scroll")))
        self.assertGreaterEqual(probe["motion"]["hero"]["layers"], 2)

        self.page.write_text('''<!doctype html><html><head><link rel="icon" href="data:,"></head>
<body><h1>Still</h1><div style="height:1600px"></div></body></html>''', encoding="utf-8")
        output = self.folder / "still"
        self.shoot(output, "--motion", "--size", "1280x800")
        issues = "\n".join(json.loads((output / "report.json").read_text(encoding="utf-8"))[0]["issues"])
        self.assertIn("首次进入：没有检测到动画", issues)
        self.assertIn("滚动：没有检测到", issues)

    def test_steps_accept_unquoted_selectors_with_spaces(self):
        self.shoot(self.folder / "unquoted", "--steps", "click body #go")
        self.shoot(self.folder / "quoted", "--steps", 'click "body #go"')

    def test_motion_skips_scroll_check_on_single_screen(self):
        self.page.write_text('''<!doctype html><html><head><link rel="icon" href="data:,"><style>
html,body{margin:0;height:100%;overflow:hidden}
@keyframes rise{from{opacity:0;transform:translateY(24px)}to{opacity:1;transform:none}}
h1{animation:rise .5s ease-out both}</style></head><body><h1>Game</h1></body></html>''', encoding="utf-8")
        output = self.folder / "single-screen"
        result = self.shoot(output, "--motion")
        probe = json.loads((output / "report.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(probe["issues"], [])
        self.assertFalse(probe["motion"]["scroll"]["scrollable"])
        self.assertIn("页面不滚动", result.stdout)

    def test_record_entry_captures_first_appearance(self):
        self.page.write_text('''<!doctype html><html><head><link rel="icon" href="data:,"><style>
body{margin:0;background:#fde68a}
@keyframes rise{from{opacity:0;transform:translateY(160px)}to{opacity:1;transform:none}}
h1{margin:40px;height:300px;background:#1e3a8a;animation:rise .3s ease-out both}</style></head>
<body><h1></h1></body></html>''', encoding="utf-8")
        # 出场 0.3 秒就结束：默认录屏开录时已经播完，只有 --entry 能录到它
        late = self.folder / "late"
        self.shoot(late, "--record", "--hold", "300")
        self.assertEqual((late / "motion-start.jpg").read_bytes(), (late / "motion-end.jpg").read_bytes())
        output = self.folder / "entry"
        self.shoot(output, "--record", "--entry", "--hold", "300")
        self.assert_artifacts(output, ("motion-start.jpg", "motion-mid.jpg", "motion-end.jpg"))
        self.assertNotEqual((output / "motion-start.jpg").read_bytes(), (output / "motion-end.jpg").read_bytes())

    def test_missing_favicon_is_not_reported_as_a_page_problem(self):
        self.page.write_text('<!doctype html><html lang="zh"><head><meta charset="utf-8"><title>小样</title></head>'
                             '<body><h1 style="font-size:32px">没有图标的小样</h1></body></html>', encoding="utf-8")
        output = self.folder / "no-favicon"
        self.shoot(output, "--size", "1280x900")
        report = json.loads((output / "report.json").read_text(encoding="utf-8"))
        self.assertEqual(report[0]["issues"], [])

    def test_reports_page_problems(self):
        self.page.write_text(self.page.read_text(encoding="utf-8").replace("</body>", '''
<div style="width:2000px">溢出</div><img src="missing.png">
<script>console.error('shoot-test-error'); throw new Error('shoot-test-exception');</script>
</body>'''), encoding="utf-8")
        for args in ((), ("--record", "--hold", "300")):
            with self.subTest(record=bool(args)):
                output = self.folder / ("problem-record" if args else "problem-shots")
                self.shoot(output, "--size", "1280x900", *args)
                report = json.loads((output / "report.json").read_text(encoding="utf-8"))
                issues = "\n".join(report[0]["issues"])
                for expected in ("shoot-test-error", "shoot-test-exception", "横向溢出", "图片没加载出来"):
                    self.assertIn(expected, issues)


    def test_lint_flags_default_patterns(self):
        self.page.write_text('''<!doctype html><html lang="zh"><head><meta charset="utf-8"><link rel="icon" href="data:,"><style>
body{margin:0;padding:40px;font:16px/1.7 sans-serif;color:#222;background:#fff}
.eyebrow{font-size:12px;letter-spacing:.12em;text-transform:uppercase;margin:0}
h1{font-size:48px;margin:0 0 24px;background:linear-gradient(90deg,#7c3aed,#06b6d4);background-clip:text;-webkit-background-clip:text;color:transparent}
.num{font-size:12px;margin:0}h2{font-size:28px;margin:4px 0 24px}
.card{border:1px solid #ddd;border-radius:16px;padding:24px;margin:16px 0}
.quote{border-left:4px solid #e11d48;padding:12px 16px}.low{color:#c8c8c8}
.dense{font-size:11px;line-height:1.2;width:600px}.ghost{opacity:0}
</style></head><body><p class="eyebrow">Smart Ledger</p><h1>让记账更简单</h1>
<p class="num">01 / 账户</p><h2>所有账户一目了然</h2>
<div class="card">外层<div class="card">里层卡片里有一些文字</div></div>
<div class="quote">好的工具让人忘记它的存在。</div><p class="low">很淡的说明文字，颜色几乎看不见。</p>
<p class="dense">这是一段很长的正文，用了很小的字号和很紧的行高，一行特别长，读的时候眼睛要跑很远才能换行，这是一段很长的正文，用了很小的字号和很紧的行高，一行特别长，读的时候眼睛要跑很远才能换行。</p>
<ul><li><span>🚀</span> 快</li><li><span>💰</span> 省</li></ul><div><span>WORKSPACE OVERVIEW</span> <span>CONTENT OBJECT</span></div>
<p class="ghost">这一段一直停在透明状态</p>
<p>这一页的中文足够多，用来判断这是中文界面。这一页的中文足够多，用来判断这是中文界面。这一页的中文足够多，用来判断这是中文界面。</p>
</body></html>''', encoding="utf-8")
        output = self.folder / "lint"
        result = self.shoot(output, "--size", "1440x900")
        entry = json.loads((output / "report.json").read_text(encoding="utf-8"))[0]
        self.assertEqual(entry["issues"], [])
        rules = {item["rule"] for item in entry["lint"]}
        for rule in ("eyebrow", "numbered", "gradientText", "sideStripe", "nestedCards", "emojiIcon",
                     "englishLabel", "contrastLow", "smallText", "tightLeading", "longMeasure", "stuck"):
            self.assertIn(rule, rules)
        self.assertIn("默认做法提示", result.stdout)

    def test_lint_requires_body_contrast_of_4_5(self):
        self.page.write_text('''<!doctype html><html><head><link rel="icon" href="data:,"><style>
body{margin:0;padding:40px;font:16px/1.6 sans-serif;color:#222;background:#fff}
.soft{color:#8a8a8a}
</style></head><body><p class="soft">Body text at about 3.5 to 1 contrast still fails the 4.5 to 1 floor.</p></body></html>''', encoding="utf-8")
        output = self.folder / "soft"
        self.shoot(output, "--size", "1280x800")
        lint = json.loads((output / "report.json").read_text(encoding="utf-8"))[0]["lint"]
        self.assertIn("contrastLow", {item["rule"] for item in lint})

    def test_lint_spares_css_triangles_and_neutral_dividers(self):
        self.page.write_text('''<!doctype html><html><head><link rel="icon" href="data:,"><style>
body{margin:0;padding:40px;font:16px/1.6 sans-serif;color:#222}
.play{width:0;height:0;border-top:12px solid transparent;border-bottom:12px solid transparent;border-left:20px solid #e11d48}
aside{border-right:1px solid #ddd;height:200px;width:200px}
@keyframes rise{from{opacity:0}to{opacity:1}}h1{animation:rise .6s ease-out both;font-size:40px;margin:48px 0 12px}
</style></head><body><span class="play"></span><aside>Sidebar</aside><p>Intro paragraph above the heading.</p>
<h1>Plain heading</h1><p>Body text that follows the heading closely.</p></body></html>''', encoding="utf-8")
        output = self.folder / "clean"
        self.shoot(output, "--size", "1280x800")
        self.assertEqual(json.loads((output / "report.json").read_text(encoding="utf-8"))[0]["lint"], [])

    def test_compare_against_reference(self):
        reference = self.folder / "reference"
        self.shoot(reference, "--states", "a", "--size", "800x600")
        same = self.folder / "same"
        self.shoot(same, "--states", "a", "--size", "800x600", "--compare", str(reference / "a.png"))
        entry = json.loads((same / "report.json").read_text(encoding="utf-8"))[0]
        self.assert_artifacts(same, ("a-compare.png",))
        self.assertLess(entry["compare"]["overall"], 1)
        other = self.folder / "other"
        result = self.shoot(other, "--states", "b", "--size", "800x600", "--compare", str(reference / "a.png"))
        entry = json.loads((other / "report.json").read_text(encoding="utf-8"))[0]
        self.assertGreater(entry["compare"]["overall"], 20)
        self.assertEqual(len(entry["compare"]["cells"]), 9)
        self.assertIn("差异明显的像素占", result.stdout)
        missing = subprocess.run([NODE, str(SCRIPT), str(self.page), "--compare", str(self.folder / "nope.png")],
                                 cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=30)
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("找不到参考图", missing.stderr)

    def test_reports_blank_webgl_canvas(self):
        # 同一块画布已经拿了 2d 上下文，再要 webgl 必然失败，用它模拟“截图成功但画布是空的”
        self.page.write_text(self.page.read_text(encoding="utf-8").replace("</body>", '''
<canvas id="bad"></canvas><canvas id="good"></canvas>
<script>
const bad = document.querySelector('#bad'); bad.getContext('2d'); bad.getContext('webgl');
const good = document.querySelector('#good'); good.getContext('webgl2') || good.getContext('webgl');
</script></body>'''), encoding="utf-8")
        output = self.folder / "webgl"
        self.shoot(output)
        issues = "\n".join(json.loads((output / "report.json").read_text(encoding="utf-8"))[0]["issues"])
        self.assertIn("WebGL：1 个画布没能创建绘图上下文", issues)

    def run_failure(self, output, *args):
        result = subprocess.run([NODE, str(SCRIPT), str(self.page), "--out", str(output), *args],
                                cwd=ROOT, env=self.env, capture_output=True, text=True, timeout=110)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        return result

    def test_fill_replaces_and_fires_events_while_type_inserts_at_cursor(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,">
<input id="insert" value="seed"><input id="replace" value="old value">
<textarea aria-label="Message box">old text</textarea><input id="empty" value="old value">
<div id="rich" contenteditable="true">old content</div><button id="verify">Verify</button>
<script>
const insert=document.querySelector('#insert'); insert.onfocus=()=>insert.setSelectionRange(2,2);
const changes={};
for(const el of document.querySelectorAll('#replace,textarea,#empty,#rich')) {
  changes[el.id||'textarea']=[];
  for(const event of ['input','change'])el.addEventListener(event,e=>{
    if(!e.bubbles)console.error('fill-event-not-bubbling');
    changes[el.id||'textarea'].push([event,el.isContentEditable?el.textContent:el.value]);
  });
}
document.querySelector('#verify').onclick=()=>{
  if(insert.value!=='seXed')console.error('type-did-not-insert-at-cursor');
  for(const [key,value] of Object.entries({replace:'new value',textarea:'new text',empty:'',rich:'new content'})) {
    const el=key==='textarea'?document.querySelector('textarea'):document.getElementById(key);
    if((el.isContentEditable?el.textContent:el.value)!==value ||
       JSON.stringify(changes[key])!==JSON.stringify([['input',value],['change',value]]))console.error('fill-failed-'+key);
  }
};</script>''')
        output = self.folder / 'fill'
        self.shoot(output, '--size', 'desktop', '--steps', 'type #insert X; fill #replace "new value"; '
                   'fill [aria-label="Message box"] "new text"; fill #empty ""; '
                   'fill #rich "new content"; click #verify')
        self.assertEqual(json.loads((output / 'report.json').read_text())[0]['issues'], [])

    def test_fill_rejects_non_editable_elements_with_context(self):
        result = self.run_failure(self.folder / 'fill-invalid', '--steps', 'fill #go hello')
        self.assertIn('第 1 步「fill #go hello」', result.stderr)
        self.assertIn('fill 需要输入框、文本域或可编辑元素', result.stderr)
        self.assertIn('例如 fill #message "hello world"', result.stderr)

    def test_click_doubleclick_and_hover_warn_about_blocker_and_still_execute(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><style>
#target,.veil{position:absolute;left:20px;top:20px;width:120px;height:60px}.veil{z-index:2}
#verify{position:absolute;left:200px;top:20px}</style>
<button id="target">Target</button><div class="veil">Overlay</div><button id="verify">Verify</button>
<script>
let clicks=0,doubles=0,hovers=0,hits=0;
document.querySelector('.veil').onclick=()=>clicks++;
document.querySelector('.veil').ondblclick=()=>doubles++;
document.querySelector('.veil').onmouseenter=()=>hovers++;
document.querySelector('#target').onclick=()=>hits++;
document.querySelector('#verify').onclick=()=>{
if(clicks!==3 || doubles!==1 || hovers!==1 || hits!==0)console.error('blocked-actions-not-executed');
};</script>''')
        output = self.folder / 'blocked'
        result = self.shoot(output, '--steps', 'hover #target; click #target; dblclick #target; click #verify')
        issues = json.loads((output / 'report.json').read_text())[0]['issues']
        self.assertEqual(len(issues), 3, issues)
        for i, action in enumerate(['hover', 'click', 'dblclick'], 1):
            message = f'第 {i} 步「{action} #target」：中心点被 div.veil 挡住'
            self.assertIn(message, issues[i - 1])
            self.assertIn(message, result.stdout)
            self.assertIn('动作仍照常执行', issues[i - 1])
        self.assertIn('发现 3 个问题', result.stdout)

    def test_descendant_at_center_is_not_a_blocker_and_mark_does_not_warn(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><style>
#target{width:120px;height:60px}#target span{display:block;width:100%;height:100%}</style>
<button id="target"><span>Target</span></button><button id="disabled" disabled>Disabled</button>
<script>let count=0;document.querySelector('#target').onclick=()=>count++;
document.querySelector('#target').ondblclick=()=>{if(count!==3)console.error('descendant-click-failed')};</script>''')
        output = self.folder / 'descendant'
        result = self.shoot(output, '--steps', 'hover #target; click #target; dblclick #target', '--mark', '#disabled')
        self.assertEqual(json.loads((output / 'report.json').read_text())[0]['issues'], [])
        self.assertNotIn('提示：第', result.stdout)

    def test_disabled_reasons_are_warnings_and_do_not_stop_actions(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><style>button{width:100px;height:50px}</style>
<button id="disabled" disabled>Disabled</button><button id="aria" aria-disabled="true">Aria</button>
<button id="pointer" style="pointer-events:none">Pointer</button>
<fieldset disabled><button id="inherited">Inherited</button></fieldset><button id="verify">Verify</button>
<script>let count=0;document.querySelector('#aria').onclick=()=>count++;
document.querySelector('#verify').onclick=()=>{if(count!==1)console.error('aria-action-stopped')};</script>''')
        output = self.folder / 'disabled'
        result = self.shoot(output, '--size', 'desktop', '--steps', 'hover #disabled; click #aria; '
                            'dblclick #pointer; click #inherited; click #verify')
        issues = json.loads((output / 'report.json').read_text())[0]['issues']
        for step, reason in [('hover #disabled', 'disabled'), ('click #aria', 'aria-disabled="true"'),
                             ('dblclick #pointer', 'pointer-events: none'), ('click #inherited', 'disabled')]:
            self.assertTrue(any(f'「{step}」：目标处于禁用状态（{reason}）' in issue for issue in issues), issues)
            self.assertIn(reason, result.stdout)
        self.assertFalse(any('控制台错误' in issue for issue in issues), issues)
        self.assertIn(f'发现 {len(issues)} 个问题', result.stdout)

    def test_action_warnings_are_in_recording_and_motion_reports(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><style>
#target,#overlay{position:absolute;top:20px;left:20px;width:100px;height:50px}#overlay{z-index:2}</style>
<button id="target" aria-disabled="true">Target</button><div id="overlay">Overlay</div>''')
        output = self.folder / 'warning-record'
        result = self.shoot(output, '--motion', '--record', '--steps', 'click #target', '--hold', '0')
        report = json.loads((output / 'report.json').read_text())
        self.assertEqual(len(report), 2)
        self.assertIn('motion', report[0])
        for entry in report:
            self.assertTrue(any('中心点被 div#overlay 挡住' in issue for issue in entry['issues']), entry)
            self.assertTrue(any('aria-disabled="true"' in issue for issue in entry['issues']), entry)
        self.assertIn('中心点被 div#overlay 挡住', result.stdout)
        self.assertIn(f"发现 {sum(len(r['issues']) for r in report)} 个问题", result.stdout)
        # A later failure must not erase warnings already raised by the recording.
        failed = self.folder / 'warning-before-failure'
        result = self.run_failure(failed, '--record', '--steps', 'click #target; click .missing', '--hold', '0')
        entry = json.loads((failed / 'report.json').read_text())[0]
        self.assertTrue(entry['failed'])
        self.assertTrue(any('中心点被 div#overlay 挡住' in issue for issue in entry['issues']), entry)
        self.assertTrue(any('aria-disabled="true"' in issue for issue in entry['issues']), entry)
        self.assertTrue(any('找不到元素 .missing' in issue for issue in entry['issues']), entry)
        self.assertIn(f"发现 {len(entry['issues'])} 个问题", result.stdout)
        self.assertEqual(list(failed.glob('frames*')), [])

    def test_missing_elements_explain_iframe_and_open_shadow_root_scope(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,">
<iframe srcdoc="<button id='framed'>Framed</button>"></iframe><iframe srcdoc="<p>Another frame</p>"></iframe>
<div id="host"></div><div id="closed"></div><script>
const root=document.querySelector('#host').attachShadow({mode:'open'});
root.innerHTML='<button id="shadowed">Shadowed</button><div id="nested"></div>';
root.querySelector('#nested').attachShadow({mode:'open'}).innerHTML='<button>Nested</button>';
document.querySelector('#closed').attachShadow({mode:'closed'}).innerHTML='<button>Closed</button>';
</script>''')
        for args in [('--steps', 'click #framed'), ('--steps', 'click text=Shadowed'),
                     ('--steps', 'fill #shadowed hello'), ('--mark', '#shadowed')]:
            with self.subTest(args=args):
                result = self.run_failure(self.folder / 'scope', *args)
                self.assertIn('找不到元素', result.stderr)
                self.assertIn('选择器只在主文档里找，页面里有 2 个 iframe、2 个开放的 shadow root', result.stderr)
        self.page.write_text('<!doctype html><link rel="icon" href="data:,"><body></body>')
        result = self.run_failure(self.folder / 'no-roots', '--steps', 'click .missing')
        self.assertNotIn('选择器只在主文档里找', result.stderr)

    def test_missing_element_scope_counts_iframes_and_shadow_roots_independently(self):
        for markup, expected in [
            ('<iframe srcdoc="<p>Frame</p>"></iframe>', '1 个 iframe、0 个开放的 shadow root'),
            ('''<div id="host"></div><script>document.querySelector('#host').attachShadow({mode:'open'}).innerHTML='<p>Shadow</p>';</script>''',
             '0 个 iframe、1 个开放的 shadow root'),
        ]:
            with self.subTest(expected=expected):
                self.page.write_text('<!doctype html><link rel="icon" href="data:,">' + markup)
                result = self.run_failure(self.folder / 'root-counts', '--steps', 'click .missing')
                self.assertIn(expected, result.stderr)

    def test_actions_text_selectors_and_marks(self):
        self.page.write_text('''<!doctype html><meta charset="utf-8"><link rel="icon" href="data:,"><style>
body{margin:0;padding:20px}button{padding:12px}.hidden{display:none}</style>
<div class="nav"><button aria-label="Send money">More</button><button id="double">Undo</button></div>
<input aria-label="Message box"><select id="choice"><option value="a">First</option><option value="b">Second item</option></select>
<div id="status"></div><button class="hidden">More</button><div data-label="a;b">Semicolon</div>
<script>
let count=0; document.querySelector('[aria-label]').onclick=()=>count++;
document.querySelector('#double').ondblclick=()=>count+=10;
document.querySelector('#double').onmouseenter=()=>document.body.dataset.hover='yes';
for(const event of ['input','change']) document.querySelector('#choice').addEventListener(event,()=>document.body.dataset[event]='yes');
setTimeout(()=>{const b=document.createElement('button');b.textContent='Ready';document.body.append(b)},700);
setInterval(()=>document.querySelector('#status').textContent=JSON.stringify({count,text:document.querySelector('input').value,choice:document.querySelector('select').value,hover:document.body.dataset.hover,input:document.body.dataset.input,change:document.body.dataset.change}),30);
</script>''', encoding="utf-8")
        output = self.folder / "actions"
        result = self.shoot(output, "--steps", 'click text=More; click [aria-label="Send money"]; '
                            'click "[aria-label=\\"Send money\\"]"; hover button:has-text("Undo"); '
                            'doubleclick button:has-text(\'Undo\'); fill [aria-label="Message box"] "hello world"; '
                            'press ControlOrMeta+A; type "body input" "replacement"; select #choice b; '
                            'select #choice "Second item"; waitfor text=Ready; sleep 0.1s; '
                            'click [data-label="a;b"]; click :has-text("Semicolon")',
                            "--mark", '1=text=More; 2=button:has-text("Undo")')
        self.assert_artifacts(output, ("page.png", "page-marked.png"))
        # The report includes console output only on errors, so assert browser state through a guard.
        self.page.write_text(self.page.read_text().replace('setInterval(()=>document', '''setTimeout(()=>{
if(count!==13 || document.querySelector('input').value!=='replacement' || document.querySelector('select').value!=='b' || document.body.dataset.hover!=='yes' || document.body.dataset.input!=='yes' || document.body.dataset.change!=='yes') console.error('action-guard-failed');
},2600);setInterval(()=>document'''))
        self.shoot(self.folder / "guard", "--steps", 'click text=More; click [aria-label="Send money"]; '
                   'click "[aria-label=\\"Send money\\"]"; hover button:has-text("Undo"); '
                   'dblclick button:has-text("Undo"); type [aria-label="Message box"] "hello world"; '
                   'key ControlOrMeta+A; type "body input" replacement; select #choice "Second item"; wait 2000')
        report = json.loads((self.folder / "guard/report.json").read_text())
        self.assertEqual(report[0]["issues"], [], result.stdout)

    def test_selector_errors_include_context_and_hints(self):
        for raw, expected in [
            ('wait 0; click [id="missing"]', ('第 2 步', 'click [id="missing"]', 'id="go"')),
            ('click text=切', ('',)),  # containment fallback succeeds, tested separately
            ('click text=切换不存在', ('可见文字', '切换')),
            ('click body .missing', ('"body" 匹配 1 个元素',)),
            ('click button:unknown', ('不是合法的 CSS', 'text="Send money"', 'button:has-text("Undo")')),
            ('waitfor .missing', ('第 1 步', 'waitfor .missing', '找不到元素')),
        ]:
            if expected == ('',):
                self.shoot(self.folder / "contained", "--steps", raw)
                continue
            with self.subTest(raw=raw):
                result = self.run_failure(self.folder / "missing", "--steps", raw)
                for text in expected:
                    self.assertIn(text, result.stderr)
        self.run_failure(self.folder / "mark-invalid", "--mark", "button:unknown")

    def test_visible_element_does_not_scroll_and_offscreen_does(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><style>
body{margin:0;height:2400px}#top{position:absolute;top:40px}#bottom{position:absolute;top:1800px}</style>
<button id="top">Top</button><button id="bottom">Bottom</button><script>
let last=0;topButton=document.querySelector('#top');topButton.onmouseenter=()=>{if(scrollY!==0)console.error('visible-scrolled')};
topButton.onclick=()=>{if(scrollY!==0)console.error('visible-scrolled')};
document.querySelector('#bottom').onclick=()=>{if(scrollY===0)console.error('offscreen-not-scrolled')};
</script>''')
        self.shoot(self.folder / "scroll", "--size", "desktop", "--steps", "hover #top; click #top; click #bottom")
        self.assertEqual(json.loads((self.folder / "scroll/report.json").read_text())[0]["issues"], [])

    def test_query_multiple_zooms_and_groups_reset(self):
        self.page.write_text(self.page.read_text().replace('</body>', '''<script>
if(new URLSearchParams(location.search).get('a')!=='1' || new URLSearchParams(location.search).get('b')!=='two words')console.error('query-missing');
document.querySelector('#go').addEventListener('click',()=>{if(document.body.dataset.state!=='b')console.error('group-not-reset')});
</script></body>'''))
        output = self.folder / "multiple"
        self.shoot(output, "--size=desktop", "--zoom=1,2", "--query", "a=1&b=two+words", "--states", "a",
                   "--steps", "open: click body #go", "--steps", "send: click #go")
        self.assert_artifacts(output, ("a-open.png", "a-send.png", "a-open-@2x.png", "a-send-@2x.png"))
        report = json.loads((output / "report.json").read_text())
        self.assertTrue(all(r["issues"] == [] for r in report))
        self.assertEqual([r["zoom"] for r in report], [1, 1, 2, 2])
        aliases = self.folder / "aliases"
        self.shoot(aliases, "--size", "phone,800x600", "--query", "a=1&b=two+words")
        self.assert_artifacts(aliases, ("page-390x844.png", "page-800x600.png"))

    def test_named_and_unnamed_record_groups(self):
        output = self.folder / "record-groups"
        self.shoot(output, "--record", "--hold=100", "--wait=0", "--steps", "open: click #go", "--steps", "click #go")
        self.assert_artifacts(output, ("motion-open-start.jpg", "motion-open-mid.jpg", "motion-open-end.jpg", "motion-2-end.jpg"))
        if shutil.which('ffmpeg'):
            self.assert_artifacts(output, ("record-open.mp4", "record-2.mp4"))
        self.assertEqual(len(json.loads((output / "report.json").read_text())), 2)

    @unittest.skipUnless(shutil.which('ffmpeg'), 'ffmpeg is needed for complete evidence')
    def test_evidence_all_artifacts_and_partial_failure(self):
        output = self.folder / "evidence"
        self.shoot(output, "--evidence", "--states=a,b", "--hold=100", "--steps", "toggle: click #go")
        self.assert_artifacts(output, ("a.png", "b.png", "a-masked.png", "b-masked.png", "sheet.png", "sheet-masked.png",
                                      "a-@2x.png", "record-entry.mp4", "motion-entry-start.jpg", "motion-entry-mid.jpg", "motion-entry-end.jpg",
                                      "record-toggle.mp4", "motion-toggle-start.jpg", "motion-toggle-mid.jpg", "motion-toggle-end.jpg"))
        report = json.loads((output / "report.json").read_text())
        self.assertTrue(any(r["file"] == "motion-probe-toggle" for r in report))
        failed = self.folder / "partial"
        result = self.run_failure(failed, "--evidence", "--hold=100", "--steps", "broken: click .missing", "--steps", "good: click #go")
        self.assert_artifacts(failed, ("page.png", "page-@2x.png", "record-entry.mp4", "record-good.mp4", "report.json"))
        report = json.loads((failed / "report.json").read_text())
        self.assertEqual(sum(r.get("failed", False) for r in report), 2)
        self.assertIn("失败", result.stdout)

    def test_browser_timeout_retries_once(self):
        if sys.platform == 'win32':
            self.skipTest('POSIX launch wrapper')
        wrapper = self.folder / 'chrome-wrapper'
        marker = self.folder / 'launch-count'
        # First launch really times out after 45 seconds; the retry uses the real browser.
        import shlex
        wrapper.write_text('#!/bin/sh\nif [ ! -f ' + shlex.quote(str(marker)) + ' ]; then\n'
                           'touch ' + shlex.quote(str(marker)) + '\nexec sleep 60\nfi\nexec '
                           + shlex.quote(self.browser) + ' "$@"\n')
        wrapper.chmod(0o755)
        env = dict(self.env, CHROME_PATH=str(wrapper))
        output = self.folder / 'retried'
        result = subprocess.run([NODE, str(SCRIPT), str(self.page), '--out', str(output)],
                                cwd=ROOT, env=env, capture_output=True, text=True, timeout=110)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('45 秒内没有启动', result.stderr)
        self.assertIn('正在重试一次', result.stderr)
        self.assertIn('同时开了很多个任务时会变慢', result.stderr)
        self.assert_artifacts(output, ('page.png', 'report.json'))
        self.assertEqual(list(self.profile_root.glob('oil-shoot-*')), [])


    def test_text_matching_priority_and_combo_modifiers(self):
        self.page.write_text('''<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1"><link rel="icon" href="data:,"><style>button{padding:12px}</style>
<section><span>More</span><button id="clickable">More</button></section>
<div><button id="deep"><span>Deep</span></button></div><button id="partial">Send money now</button>
<div hidden><div><button>More</button></div></div><input id="edit"><button id="verify">Verify</button>
<script>
const hits=[];for(const id of ['clickable','deep','partial'])document.getElementById(id).onclick=()=>hits.push(id);
const keys=[];addEventListener('keydown',e=>keys.push([e.key,e.metaKey,e.ctrlKey,e.shiftKey]));
document.getElementById('verify').onclick=()=>{
if(hits.join(',')!=='clickable,deep,partial')console.error('text-priority-failed');
if(!keys.some(k=>k[0]==='A'&&k[1]) || !keys.some(k=>k[0]==='A'&&k[2]) || !keys.some(k=>k[0]==='Tab'&&k[3]))console.error('combo-modifiers-failed');
};</script>''')
        output = self.folder / 'priorities'
        self.shoot(output, '--steps', 'click text=More; click text=Deep; click text="Send money"; '
                   'click #edit; key Meta+A; key Control+A; key Shift+Tab; click #verify')
        self.assertEqual(json.loads((output / 'report.json').read_text())[0]['issues'], [])

    def test_mark_and_waitfor_accept_later_visible_css_match(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><button class="target" hidden>Hidden</button>
<button class="target">Shown</button><div class="delayed" style="opacity:0">Ready</div>
<script>setTimeout(()=>document.querySelector('.delayed').style.opacity=1,800)</script>''')
        output = self.folder / 'visible-match'
        self.shoot(output, '--steps', 'waitfor .target; waitfor .delayed', '--mark', '.target')
        self.assert_artifacts(output, ('page.png', 'page-marked.png'))

    def test_single_zoom_preserves_sheet_and_record_names(self):
        output = self.folder / 'zoom-sheet'
        self.shoot(output, '--zoom', '2', '--states', 'a,b', '--sheet', '--mask')
        self.assert_artifacts(output, ('a-@2x.png', 'sheet.png', 'sheet-masked.png'))
        output = self.folder / 'zoom-record'
        self.shoot(output, '--zoom', '2', '--record', '--steps', 'click #go', '--hold', '100')
        self.assert_artifacts(output, ('motion-start.jpg', 'motion-mid.jpg', 'motion-end.jpg'))
        if shutil.which('ffmpeg'):
            self.assert_artifacts(output, ('record.mp4',))


    def test_hidden_matches_report_count_reason_and_visible_example(self):
        cases = [
            ('style="display:none"', '', 'display:none'),
            ('style="visibility:hidden"', '', 'visibility:hidden'),
            ('style="width:0;height:0;padding:0;border:0"', '', '尺寸为 0'),
            ('', 'display:none', '被祖先 div#container 隐藏：display:none'),
            ('', 'visibility:hidden', '被祖先 div#container 隐藏：visibility:hidden'),
            ('', 'opacity:0', '被祖先 div#container 隐藏：opacity:0'),
        ]
        for attrs, parent_style, reason in cases:
            with self.subTest(reason=reason):
                self.page.write_text(f'''<!doctype html><link rel="icon" href="data:,">
<button data-view="activity">Activity</button><div id="container" style="{parent_style}">
<button data-view="accounts" {attrs}>Accounts</button><button data-view="accounts" {attrs}>Accounts 2</button></div>''')
                result = self.run_failure(self.folder / 'hidden', '--size', 'desktop', '--steps', 'click [data-view="accounts"]')
                self.assertIn('匹配到 2 个元素', result.stderr)
                self.assertIn(reason, result.stderr)
                self.assertNotIn('找不到元素', result.stderr)
                self.assertIn('先执行让它出现的那一步', result.stderr)
                self.assertIn('例如 click [data-view="activity"]', result.stderr)
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><details id="tools">
<summary>Tools</summary><button data-view="accounts">Accounts</button></details>''')
        result = self.run_failure(self.folder / 'closed', '--steps', 'click [data-view="accounts"]')
        self.assertIn('details 未展开', result.stderr)
        self.assertIn('例如 click #tools > summary', result.stderr)
        self.shoot(self.folder / 'opened', '--steps', 'click #tools > summary; click [data-view="accounts"]')

    def test_all_actions_choose_first_visible_match(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><style>button{padding:12px}</style>
<button class="target" hidden>Hidden</button><button class="target">Visible</button>
<input class="edit" hidden><input class="edit">
<select class="choice" hidden><option value="a">A</option><option value="b">B</option></select>
<select class="choice"><option value="a">A</option><option value="b">B</option></select>
<button class="double" hidden>Hidden</button><button class="double">Double</button>
<div class="drag" hidden>Hidden</div><div class="drag" style="width:100px;height:50px">Drag</div>
<button id="verify">Verify</button><script>
let clicked=0, hovered=false, doubled=false, dragged=false;
const target=document.querySelectorAll('.target')[1]; target.onclick=()=>clicked++; target.onmouseenter=()=>hovered=true;
document.querySelectorAll('.double')[1].ondblclick=()=>doubled=true;
document.querySelectorAll('.drag')[1].onmousedown=()=>dragged=true;
document.querySelector('#verify').onclick=()=>{
if(clicked!==1 || !hovered || !doubled || !dragged || document.querySelectorAll('.edit')[1].value!=='hello' || document.querySelectorAll('.choice')[1].value!=='b')console.error('visible-match-failed');
};</script>''')
        output = self.folder / 'visible-actions'
        self.shoot(output, '--size', 'desktop', '--steps', 'hover .target; click .target; dblclick .double; '
                   'drag .drag 20 0; type .edit hello; select .choice b; waitfor .target; click #verify')
        self.assertEqual(json.loads((output / 'report.json').read_text())[0]['issues'], [])
        # waitfor must continue polling matches that exist but are still hidden.
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><button id="later" hidden>Ready</button>
<script>setTimeout(()=>document.querySelector('#later').hidden=false,700)</script>''')
        self.shoot(self.folder / 'later', '--wait', '0', '--steps', 'waitfor #later; click #later')

    def test_selector_examples_and_text_hints_are_relevant(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><p>Alpha unrelated</p><p>Send monez</p>
<button>Send money now</button><button data-view="accounts" hidden>Hidden</button>
<button data-view="activity">Activity</button>''')
        for action in ['click', 'hover', 'dblclick']:
            result = self.run_failure(self.folder / action, '--steps', action + ' [data-view="missing"]')
            self.assertIn('例如 ' + action + ' [data-view="activity"]', result.stderr)
            self.assertLess(result.stderr.index('data-view="activity"'), result.stderr.index('data-view="accounts"'))
        # A CSS-filtered text search misses; the text hint search covers the visible page.
        result = self.run_failure(self.folder / 'text-hint', '--steps', 'click a:has-text("Send money")')
        self.assertIn('例如 click text="Send money now"', result.stderr)
        self.assertLess(result.stderr.index('可见文字 "Send money now"'), result.stderr.index('可见文字 "Send monez"'))
        result = self.run_failure(self.folder / 'similar', '--steps', 'click text="Send monet"')
        self.assertLess(result.stderr.index('可见文字 "Send monez"'), result.stderr.index('可见文字 "Alpha unrelated"'))
        self.page.write_text('<!doctype html><link rel="icon" href="data:,"><body></body>')
        result = self.run_failure(self.folder / 'generic', '--steps', 'hover .missing')
        self.assertIn('例如 hover .open', result.stderr)

    def test_failed_recordings_clean_temporary_frames(self):
        failed = self.folder / 'failed-record'
        self.run_failure(failed, '--record', '--steps', 'broken: click .missing', '--hold', '0')
        self.assertEqual(list(failed.glob('frames*')), [])
        failed = self.folder / 'failed-evidence'
        self.run_failure(failed, '--evidence', '--steps', 'broken: click .missing', '--steps', 'good: click #go', '--hold', '100')
        self.assertEqual(list(failed.glob('frames*')), [])
        self.assert_artifacts(failed, ('motion-good-end.jpg', 'motion-entry-end.jpg', 'report.json'))
        self.assertEqual(list(self.profile_root.glob('oil-shoot-*')), [])

    def test_cdp_request_times_out_in_current_step(self):
        import time
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><button id="prepare">Prepare</button><button id="hang">Hang</button>
<script>document.querySelector('#prepare').onclick=()=>{document.querySelector('#hang').getBoundingClientRect=()=>{while(true){}}};</script>''')
        output = self.folder / 'cdp-timeout'
        start = time.monotonic()
        result = self.run_failure(output, '--size', 'desktop', '--steps', 'click #prepare; click #hang')
        elapsed = time.monotonic() - start
        self.assertIn('第 2 步「click #hang」', result.stderr)
        self.assertIn('CDP Runtime.evaluate 30 秒内没有响应', result.stderr)
        self.assertGreaterEqual(elapsed, 30)
        self.assertLess(elapsed, 42)
        self.assertEqual(list(self.profile_root.glob('oil-shoot-*')), [])
        self.assertTrue(any(r.get('failed') for r in json.loads((output / 'report.json').read_text())))

    def test_browser_disconnect_interrupts_steps_and_cleans_frames(self):
        if sys.platform == 'win32':
            self.skipTest('POSIX browser kill wrapper')
        import http.server
        import shlex
        import signal
        import threading
        import time
        pid_file = self.folder / 'chrome.pid'
        wrapper = self.folder / 'chrome-kill-wrapper'
        wrapper.write_text('#!/bin/sh\necho $$ > ' + shlex.quote(str(pid_file)) + '\nexec ' + shlex.quote(self.browser) + ' "$@"\n')
        wrapper.chmod(0o755)

        class KillHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(handler):
                handler.send_response(200)
                handler.end_headers()
                handler.wfile.write(b'ok')
                os.kill(int(pid_file.read_text().strip()), signal.SIGKILL)

            def log_message(*args):
                pass

        server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), KillHandler)
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        self.page.write_text(f'''<!doctype html><link rel="icon" href="data:,"><button id="kill">Kill</button>
<script>document.querySelector('#kill').onclick=()=>setTimeout(()=>fetch('http://127.0.0.1:{server.server_port}/kill'),250);</script>''')
        output = self.folder / 'disconnect'
        start = time.monotonic()
        result = subprocess.run([NODE, str(SCRIPT), str(self.page), '--out', str(output), '--record', '--hold', '100',
                                 '--steps', 'disconnect: click #kill; wait 60000'], cwd=ROOT,
                                env=dict(self.env, CHROME_PATH=str(wrapper)), capture_output=True, text=True, timeout=15)
        self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
        self.assertIn('第 2 步「wait 60000」', result.stderr)
        self.assertIn('浏览器连接意外断开', result.stderr)
        self.assertLess(time.monotonic() - start, 10)
        self.assertEqual(list(output.glob('frames*')), [])
        self.assertEqual(list(self.profile_root.glob('oil-shoot-*')), [])

    def test_deduplicated_states_sizes_zooms_and_filename_collisions(self):
        output = self.folder / 'deduplicated'
        result = self.shoot(output, '--states', 'a,a,b,a', '--size', 'desktop,1440x900,phone,mobile', '--zoom', '1,1.0')
        report = json.loads((output / 'report.json').read_text())
        self.assertEqual(len(report), 4)
        for message in ['状态 已去重', '尺寸 已去重', '倍数 已去重']:
            self.assertIn(message, result.stdout)
        output = self.folder / 'group-collision'
        result = self.shoot(output, '--states', 'a,a-b', '--steps', 'b-c: wait 0', '--steps', 'c: wait 0')
        report = json.loads((output / 'report.json').read_text())
        self.assertEqual(len(set(r['file'] for r in report)), 4)
        self.assert_artifacts(output, ('a-b-c.png', 'a-b-c-2.png'))
        self.assertIn('文件名冲突，已区分', result.stdout)
        output = self.folder / 'variant-collision'
        self.shoot(output, '--states', 'a,a-masked,sheet', '--mask', '--sheet')
        self.assert_artifacts(output, ('a.png', 'a-masked.png', 'a-masked-2.png', 'a-masked-2-masked.png',
                                       'sheet.png', 'sheet-masked.png', 'sheet-2.png', 'sheet-2-masked.png'))
        self.assertEqual(len(list(output.glob('*.png'))), 8)

    def test_screenshots_wait_for_finite_animations_and_transitions(self):
        self.page.write_text('''<!doctype html><link rel="icon" href="data:,"><style>
@keyframes enter{from{opacity:0;transform:translateY(40px)}to{opacity:1;transform:none}}
#box{animation:enter .7s both;transition:margin-left .6s;margin-left:0}
</style><button id="go">Go</button><div id="box">Ready</div><script>
document.querySelector('#go').onclick=()=>document.querySelector('#box').style.marginLeft='100px';
const capture=()=>{
const s=getComputedStyle(document.querySelector('#box'));
if(s.opacity!=='1'||parseFloat(s.marginLeft)<99)console.error('captured-during-motion');
};
// Screenshot checks read document.images; inspect its computed styles at that moment.
Object.defineProperty(document,'images',{get(){capture();return []}});
</script>''')
        output = self.folder / 'settled'
        self.shoot(output, '--wait', '0', '--steps', 'click #go')
        report = json.loads((output / 'report.json').read_text())[0]
        self.assertEqual(report['issues'], [])
        self.assertGreater(report['animationWaitMs'], 600)
        self.assertLessEqual(report['animationWaitMs'], 2000)

    def test_screenshot_animation_wait_is_capped_and_skips_infinite(self):
        for duration, expected_wait in [('60s', True), ('60s infinite', False)]:
            with self.subTest(duration=duration):
                self.page.write_text(f'''<!doctype html><link rel="icon" href="data:,"><style>
@keyframes slow{{from{{transform:translateX(0)}}to{{transform:translateX(100px)}}}}
div{{animation:slow {duration}}}</style><div>Ready</div>''')
                output = self.folder / ('finite-cap' if expected_wait else 'infinite')
                self.shoot(output, '--wait', '0')
                waited = json.loads((output / 'report.json').read_text())[0]['animationWaitMs']
                if expected_wait:
                    self.assertGreaterEqual(waited, 1950)
                    self.assertLessEqual(waited, 2000)
                else:
                    self.assertEqual(waited, 0)
        # Recording and motion probing continue to observe the animation from the original timing.
        output = self.folder / 'record-unsettled'
        self.shoot(output, '--wait', '0', '--record', '--entry', '--hold', '100')
        report = json.loads((output / 'report.json').read_text())[0]
        self.assertNotIn('animationWaitMs', report)
        self.shoot(self.folder / 'motion-unsettled', '--wait', '0', '--motion')
        report = json.loads((self.folder / 'motion-unsettled/report.json').read_text())[0]
        self.assertNotIn('animationWaitMs', report)


    def test_disconnect_during_encoding_exits_without_waiting_for_encoder(self):
        if sys.platform == 'win32':
            self.skipTest('POSIX browser and encoder wrappers')
        import shlex
        import signal
        import threading
        import time
        browser_pid = self.folder / 'browser.pid'
        wrapper = self.folder / 'chrome-encoder-wrapper'
        wrapper.write_text('#!/bin/sh\necho $$ > ' + shlex.quote(str(browser_pid)) + '\nexec ' + shlex.quote(self.browser) + ' "$@"\n')
        wrapper.chmod(0o755)
        encoder_ready = self.folder / 'encoder-ready'
        fake_bin = self.folder / 'bin'
        fake_bin.mkdir()
        encoder = fake_bin / 'ffmpeg'
        encoder.write_text('#!/bin/sh\nif [ "$1" = "-version" ]; then exit 0; fi\n'
                           'touch ' + shlex.quote(str(encoder_ready)) + '\nexec sleep 60\n')
        encoder.chmod(0o755)
        stop = threading.Event()

        def disconnect():
            while not stop.wait(.02):
                if encoder_ready.exists():
                    os.kill(int(browser_pid.read_text()), signal.SIGKILL)
                    return

        thread = threading.Thread(target=disconnect, daemon=True)
        thread.start()
        try:
            output = self.folder / 'encoder-disconnect'
            start = time.monotonic()
            result = subprocess.run([NODE, str(SCRIPT), str(self.page), '--out', str(output), '--record', '--hold', '100'],
                                    cwd=ROOT, env=dict(self.env, CHROME_PATH=str(wrapper), PATH=str(fake_bin) + os.pathsep + os.environ['PATH']),
                                    capture_output=True, text=True, timeout=15)
            self.assertEqual(result.returncode, 1, result.stdout + result.stderr)
            self.assertTrue(encoder_ready.exists())
            self.assertIn('浏览器连接意外断开', result.stderr)
            self.assertLess(time.monotonic() - start, 10)
            self.assertEqual(list(output.glob('frames*')), [])
            self.assertEqual(list(self.profile_root.glob('oil-shoot-*')), [])
        finally:
            stop.set()
            thread.join(1)



if __name__ == "__main__":
    unittest.main()
