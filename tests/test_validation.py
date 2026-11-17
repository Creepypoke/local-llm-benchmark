import unittest, json
from llbench.common import config, ROOT
from llbench.validate import validate


class ValidationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.c = config()
        cls.tasks = {
            t["id"]: t
            for t in json.loads(
                (ROOT / "benchmarks/tasks.json").read_text(encoding="utf-8")
            )
        }

    def check(self, n, text):
        return validate(self.tasks[n], text, self.c)

    def test_arithmetic_exact(self):
        self.assertTrue(self.check(1, "1015")["passed"])
        self.assertFalse(self.check(1, "Ответ 1015")["passed"])
        self.assertFalse(self.check(1, "1005")["passed"])

    def test_json_structure_and_types(self):
        ref = json.dumps(self.tasks[5]["reference"], ensure_ascii=False)
        self.assertTrue(self.check(5, ref)["passed"])
        self.assertFalse(self.check(5, "```json\n" + ref + "\n```")["passed"])
        self.assertFalse(self.check(5, ref.replace("303000", "303001"))["passed"])

    def test_sequence_exact(self):
        value = "\n".join(map(str, range(1, 501)))
        self.assertTrue(self.check(8, value)["passed"])
        self.assertTrue(self.check(8, value + "\n")["passed"])
        self.assertFalse(self.check(8, "\n" + value)["passed"])
        self.assertFalse(self.check(8, value + "\n\n")["passed"])
        self.assertFalse(self.check(8, value + "\n501")["passed"])
        self.assertFalse(self.check(8, value.replace("250\n", ""))["passed"])

    def test_semantic_never_keyword_pass(self):
        for n in [2, 4]:
            self.assertIsNone(
                self.check(
                    n, "Яблоки Апельсины stale closure setCount(prev => prev + 1)"
                )["passed"]
            )

    def test_capacity_needs_calculation(self):
        self.assertIsNone(self.check(7, "30")["passed"])
        self.assertTrue(self.check(7, "120 × 0.25 = 30")["passed"])
        self.assertIsNone(self.check(7, "120 × 0.25 = 300")["passed"])

    def test_lis_execution_random_and_scaling(self):
        code = """```typescript
function lis(nums: number[]): number {
const tails:number[]=[];
for (const x of nums) { let lo=0,hi=tails.length; while(lo<hi){const mid=(lo+hi)>>>1;if(tails[mid]<x)lo=mid+1;else hi=mid;}tails[lo]=x; } return tails.length;
}
```"""
        result = self.check(3, code)
        self.assertTrue(result["automatic_checks_passed"], result)
        self.assertIsNone(result["passed"])
        self.assertEqual(result["detail"]["functional_cases"], 105)

    def test_lis_rejects_nondecreasing(self):
        code = """```ts
function lis(a: number[]): number { const t:number[]=[];for(const x of a){let l=0,r=t.length;while(l<r){let m=(l+r)>>1;if(t[m]<=x)l=m+1;else r=m;}t[l]=x;}return t.length;}
```"""
        self.assertFalse(self.check(3, code)["passed"])

    def test_json_item_order(self):
        value = json.loads(json.dumps(self.tasks[5]["reference"]))
        value["items"].reverse()
        self.assertTrue(self.check(5, json.dumps(value, ensure_ascii=False))["passed"])

    def test_typescript_default_export_unicode(self):
        code = """```ts
// Минимальные хвосты: первый элемент ≥ x; ответ → длина.
export default function(nums: number[]): number {
const tails:number[]=[];
for(const x of nums){let l=0,r=tails.length;while(l<r){const m=(l+r)>>>1;if(tails[m]<x)l=m+1;else r=m;}tails[l]=x;}
return tails.length;
}
```"""
        self.assertTrue(self.check(3, code)["automatic_checks_passed"])

    def test_context_exact(self):
        self.assertTrue(self.check(9, "LOG-00931")["passed"])
        self.assertFalse(self.check(9, "LOG-00987")["passed"])


if __name__ == "__main__":
    unittest.main()
