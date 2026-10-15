#!/usr/bin/env python3
"""What a merged line-status document becomes in the registry, and what a
deposit carries forward from it.

Two defects these tests exist for, both found on 2026-10-15:

  * a correction published under `line-status-<line>` was erased for every
    consumer of the NEXT deposited layer: deposit.yml wrote a fresh baseline
    (line, counter, issued-at, support-until — nothing else) whose counter
    followed the deposit counter, so it outranked the correction while saying
    none of what the correction said;
  * nothing could re-create a document the registry had lost — the only path
    was a person re-typing JSON into a workflow input.

Run: python3 -m unittest discover -s tools -p 'test_*.py'
"""
import importlib.util
import pathlib
import unittest

spec = importlib.util.spec_from_file_location(
    "line_status", pathlib.Path(__file__).parent / "line-status.py"
)
ls = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ls)

KP_ORDEAL = {
    "id": "GHSA-xfxf-qxr3-435x",
    "title": "ordeal: shifts mis-encoded",
    "severity": "high",
    "affected": ["2026.09.2", "2026.09.16"],
}


def doc(counter, kps=(), yanked=None, support="2026-10-30", line="2026.09"):
    d = {"line": line, "counter": counter, "issued-at": "2026-10-15T00:00:00Z",
         "support-until": support}
    if kps:
        d["known-problems"] = list(kps)
    if yanked:
        d["yanked"] = yanked
    return d


class PlanPublish(unittest.TestCase):
    def test_nothing_published_is_published_and_that_is_the_recreate_path(self):
        act, out = ls.plan_publish(doc(1, [KP_ORDEAL]), None, newest_baseline=21,
                                   issued_at="2026-10-15T06:00:00Z")
        self.assertEqual(act, "publish")
        self.assertEqual(out["counter"], 22)
        self.assertEqual(out["known-problems"], [KP_ORDEAL])
        self.assertEqual(out["issued-at"], "2026-10-15T06:00:00Z")

    def test_counter_rises_above_published_and_every_baseline(self):
        _, out = ls.plan_publish(doc(5, [KP_ORDEAL]), doc(30), newest_baseline=21,
                                 issued_at="t")
        self.assertEqual(out["counter"], 31)
        _, out = ls.plan_publish(doc(40, [KP_ORDEAL]), doc(30), newest_baseline=21,
                                 issued_at="t")
        self.assertEqual(out["counter"], 40)

    def test_same_content_already_published_is_a_no_op(self):
        # Running the publish job again — or on a schedule — must not mint a
        # new counter for a document that says nothing new.
        act, out = ls.plan_publish(doc(3, [KP_ORDEAL]), doc(27, [KP_ORDEAL]),
                                   newest_baseline=21, issued_at="t")
        self.assertEqual(act, "up-to-date")
        self.assertIsNone(out)

    def test_published_content_the_repo_does_not_hold_refuses(self):
        # Someone published out of band: the repository no longer describes
        # the registry. Overwriting would silently drop what they said.
        other = dict(KP_ORDEAL, id="GHSA-other")
        act, out = ls.plan_publish(doc(3, [KP_ORDEAL]), doc(27, [KP_ORDEAL, other]),
                                   newest_baseline=21, issued_at="t")
        self.assertEqual(act, "registry-has-more")
        self.assertIsNone(out)

    def test_a_published_baseline_with_nothing_said_is_superseded(self):
        # The line's published document may be a plain baseline-shaped one;
        # the repo saying more is an ordinary publish, not a conflict.
        act, out = ls.plan_publish(doc(1, [KP_ORDEAL]), doc(20), newest_baseline=21,
                                   issued_at="t")
        self.assertEqual(act, "publish")
        self.assertEqual(out["counter"], 22)

    def test_line_mismatch_refuses(self):
        with self.assertRaises(ls.Refused):
            ls.plan_publish(doc(1, [KP_ORDEAL]), doc(5, line="2026.10"),
                            newest_baseline=1, issued_at="t")


class CarryForward(unittest.TestCase):
    def test_a_deposit_baseline_carries_the_published_problems_and_yanks(self):
        pub = doc(25, [KP_ORDEAL], yanked={"2026.09.3": "broken"})
        out = ls.carry_forward(pub, line="2026.09", counter=21,
                               issued_at="2026-10-15T07:00:00Z",
                               support_until="2026-11-15")
        self.assertEqual(out["known-problems"], [KP_ORDEAL])
        self.assertEqual(out["yanked"], {"2026.09.3": "broken"})
        self.assertEqual(out["support-until"], "2026-11-15")
        # Above the published document, or a consumer keeps the correction
        # for the old layers but this baseline outranks nothing — and below
        # it the published one would win for THIS layer, with ITS window.
        self.assertEqual(out["counter"], 26)

    def test_no_published_document_is_the_plain_baseline(self):
        out = ls.carry_forward(None, line="2026.10", counter=19, issued_at="t",
                               support_until="2026-11-15")
        self.assertEqual(out, {"line": "2026.10", "counter": 19, "issued-at": "t",
                               "support-until": "2026-11-15"})

    def test_deposit_counter_wins_when_it_is_higher(self):
        out = ls.carry_forward(doc(4, [KP_ORDEAL]), line="2026.09", counter=21,
                               issued_at="t", support_until="s")
        self.assertEqual(out["counter"], 21)

    def test_a_published_document_for_another_line_refuses(self):
        with self.assertRaises(ls.Refused):
            ls.carry_forward(doc(4, line="2026.10"), line="2026.09", counter=1,
                             issued_at="t", support_until="s")


if __name__ == "__main__":
    unittest.main()
