import hashlib
import tempfile
import unittest
from datetime import date
from pathlib import Path
from unittest.mock import patch

from reportlab.pdfgen import canvas
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import Table, TableStyle

from lab_parser.catalog import CATALOG, normalize_unit, resolve_name
from lab_parser.cli import main
from lab_parser.models import Document, Page, Row
from lab_parser.parsing import parse_document
from lab_parser.pipeline import parse_batch, validate_policy


def text_document(*lines):
    return Document("doc-1", "example.pdf", "fake-hash", [Page(1, 600, 800, "pdf_text",
                    [Row(line, [10, i * 15, 500, i * 15 + 12]) for i, line in enumerate(lines)])])


def table_document(headers, rows):
    cells = [headers, *rows]
    return Document("doc-1", "example.pdf", "fake-hash", [Page(1, 600, 800, "pdf_text",
                    [Row(" | ".join(c), [10, i * 15, 500, i * 15 + 12], cells=c, table_id=0)
                     for i, c in enumerate(cells)])])


class RowParsingTests(unittest.TestCase):
    def parse(self, document, policy=None):
        return parse_document(document, date(2026, 10, 3), policy or {})

    def test_aliases_cover_35_features(self):
        self.assertEqual(len(CATALOG["aliases"]), 35)
        self.assertEqual(resolve_name("Гемоглобин (HGB)"), "hemoglobin")
        self.assertEqual(resolve_name("Активный витамин В12"), "active_B12")
        self.assertIsNone(resolve_name("Гликированный гемоглобин"))
        self.assertIsNone(resolve_name("Билирубин общий"))
        self.assertIsNone(resolve_name("RDW-SD"))

    def test_decimal_censored_value_and_reference(self):
        m = self.parse(text_document("Дата забора: 01.10.2026", "Ферритин <5,2 нг/мл 10 - 150"))[0]
        self.assertEqual((m.value, m.comparator, m.unit), (5.2, "<", "ng/mL"))
        self.assertEqual(m.reference, "10 - 150")
        self.assertEqual(m.collected_on, "2026-10-01")
        self.assertEqual(m.age_days, 2)
        self.assertIsNone(m.normalized_value)

    def test_b12_number_in_name_is_not_result(self):
        results = self.parse(text_document("Витамин B12 350 пг/мл 180 - 900", "Витамин B6 14 нг/мл 5 - 50"))
        self.assertEqual([(m.analyte, m.value) for m in results], [("vitamin_B12", 350), ("vitamin_B6", 14)])

    def test_reference_before_result_uses_header(self):
        d = table_document(["Показатель", "Референсные значения", "Результат", "Ед. изм."],
                           [["Гемоглобин", "120 - 160", "118", "г/л"]])
        m = self.parse(d)[0]
        self.assertEqual(m.value, 118)
        self.assertEqual(m.reference, "120 - 160")
        self.assertNotIn("inferred_column_order", m.issues)

    def test_unit_exponent_is_not_result(self):
        results = self.parse(text_document("Тромбоциты 220 10^9/л 150 - 400", "Эритроциты 4,5 х10^12/л 4 - 5"))
        self.assertEqual([m.value for m in results], [220, 4.5])
        self.assertEqual([m.unit for m in results], ["10^9/L", "10^12/L"])

    def test_date_of_birth_and_report_date_are_not_collection_date(self):
        results = self.parse(text_document("Дата рождения: 01.01.1980", "Дата выдачи: 02.10.2026", "Hb 135 г/л 120 - 160"))
        self.assertIsNone(results[0].collected_on)
        self.assertEqual(results[0].freshness, "unknown_date")

    def test_multiple_date_sections_stay_in_one_document(self):
        results = self.parse(text_document("Дата забора: 01.09.2026", "Hb 110 г/л", "Дата забора: 01.10.2026", "Hb 130 г/л"))
        self.assertEqual([m.collected_on for m in results], ["2026-09-01", "2026-10-01"])

    def test_invalid_date_does_not_become_upload_date(self):
        m = self.parse(table_document(["Показатель", "Результат", "Единицы", "Дата забора"],
                                     [["Hb", "130", "г/л", "31.02.2026"]]))[0]
        self.assertIsNone(m.collected_on)
        self.assertIn("invalid_collection_date", m.issues)

    def test_staleness_requires_explicit_policy(self):
        d = text_document("Дата забора: 01.09.2026", "Hb 130 г/л")
        self.assertEqual(self.parse(d)[0].freshness, "no_policy")
        self.assertEqual(self.parse(d, {"hemoglobin": 30})[0].freshness, "stale")
        self.assertEqual(self.parse(d, {"hemoglobin": 32})[0].freshness, "within_policy")

    def test_future_date(self):
        m = self.parse(text_document("Дата забора: 04.10.2026", "Hb 130 г/л"))[0]
        self.assertEqual(m.freshness, "future_date")

    def test_missing_and_unknown_units(self):
        results = self.parse(table_document(["Показатель", "Результат", "Ед. изм."],
                                           [["Hb", "130", ""], ["Ферритин", "20", "попугаи"]]))
        self.assertIsNone(results[0].unit)
        self.assertIn("unit_missing", results[0].issues)
        self.assertIn("unknown_unit", results[1].issues)

    def test_unknown_analyte_is_preserved(self):
        m = self.parse(text_document("Глюкоза 5,4 ммоль/л 3,5 - 6"))[0]
        self.assertEqual(m.raw_name, "Глюкоза")
        self.assertIsNone(m.analyte)
        self.assertEqual(m.value, 5.4)

    def test_missing_value_is_not_zero(self):
        m = self.parse(table_document(["Показатель", "Результат", "Единицы"], [["Hb", "—", "г/л"]]))[0]
        self.assertIsNone(m.value)

    def test_ambiguous_number_is_not_first_number(self):
        for value in ["12-14", "1 234", "12,3,4"]:
            m = self.parse(table_document(["Показатель", "Результат", "Единицы"], [["Hb", value, "г/л"]]))[0]
            self.assertIsNone(m.value, value)
            self.assertIn("ambiguous_numeric_result", m.issues)

    def test_unit_spelling_only_no_conversion(self):
        m = self.parse(text_document("Hb 13,5 г/дл"))[0]
        self.assertEqual((m.value, m.unit), (13.5, "g/dL"))
        self.assertIsNone(m.normalized_value)
        self.assertEqual(normalize_unit("10⁹/л"), "10^9/L")
        self.assertEqual(normalize_unit("L/L"), "L/L")
        self.assertEqual(normalize_unit("μg/L"), "µg/L")
        self.assertEqual(normalize_unit("мл/мин/1,73м^2"), "mL/min/1.73m²")

    def test_flag_is_not_a_unit(self):
        m = self.parse(text_document("Hb 118* г/л 120-160"))[0]
        self.assertEqual(m.unit, "g/L")
        self.assertEqual(m.reference, "120-160")

    def test_invalid_policies(self):
        for policy in [{"Hb": 30}, {"hemoglobin": -1}, {"hemoglobin": True}, [30]]:
            with self.assertRaises(ValueError):
                validate_policy(policy)

    def test_urine_cells_do_not_become_blood_features(self):
        results = self.parse(text_document("Вид материала: Средняя порция утренней мочи Валидация (врач):",
                                           "Эритроциты 1,0 клет/мкл 0 - 11"))
        self.assertEqual(len(results), 1)
        self.assertIsNone(results[0].analyte)
        self.assertIn("specimen_not_supported_by_model", results[0].issues)

    def test_concentration_uses_section_title_and_provenance(self):
        d = table_document(["Показатель", "Результат", "Референсные значения"],
                           [["Тиреотропный гормон (ТТГ)", "", ""],
                            ["Концентрация", "2,5 мкМЕ/мл", "0,27 - 4,2"],
                            ["Простатспецифический антиген", "", ""],
                            ["Концентрация", "0,4 нг/мл", "0 - 4"]])
        results = self.parse(d)
        self.assertEqual(len(results), 2)
        self.assertEqual((results[0].analyte, results[0].value), ("TSH", 2.5))
        self.assertIn("Тиреотропный", results[0].source.context_text)
        self.assertIsNone(results[1].analyte)

    def test_narrative_numbers_are_not_lab_rows(self):
        d = table_document(["Показатель", "Результат", "Референсные значения"],
                           [["Гемоглобин", "130 г/л", "120 - 160"],
                            ["Пример пересчета", "8 клет/мкл", ""]])
        self.assertEqual(len(self.parse(d)), 1)
        self.assertEqual(self.parse(text_document("Уровень общего ПСА у Вас в крови составляет 0,916 нг/мл")), [])

    def test_collection_label_with_slash_and_material(self):
        m = self.parse(text_document("Дата/время взятия материала: 01/01/2026 08:30", "Hb 130 г/л"))[0]
        self.assertEqual(m.collected_on, "2026-01-01")

    def test_invalid_date_resets_section_date(self):
        results = self.parse(text_document("Дата забора: 01.10.2026", "Hb 130 г/л",
                                           "Дата забора: 31.02.2026", "Hb 120 г/л"))
        self.assertIsNone(results[1].collected_on)

    def test_non_finite_numeric_results(self):
        m = self.parse(table_document(["Показатель", "Результат", "Единицы"], [["Hb", "1e999", "г/л"]]))[0]
        self.assertIsNone(m.value)
        self.assertIn("non_finite_result", m.issues)


class PDFIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fonts = [Path("/System/Library/Fonts/Supplemental/Arial.ttf"),
                 Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")]
        font = next((p for p in fonts if p.exists()), None)
        if font is None:
            raise unittest.SkipTest("A Cyrillic TTF font is required for PDF fixtures")
        pdfmetrics.registerFont(TTFont("TestCyrillic", str(font)))

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)

    def tearDown(self):
        self.temp.cleanup()

    def make_pdf(self, name="lab.pdf", collection="01.10.2026", value="130", patient="Тестовый Пациент"):
        path = self.root / name
        c = canvas.Canvas(str(path))
        c.setFont("TestCyrillic", 10)
        c.drawString(40, 790, f"Пациент: {patient}")
        c.drawString(40, 770, f"Дата забора: {collection}")
        table = Table([["Показатель", "Норма", "Результат", "Ед. изм."],
                       ["Гемоглобин", "120 - 160", value, "г/л"],
                       ["Ферритин", "10 - 150", "<5,2", "нг/мл"]], colWidths=[180, 100, 90, 100])
        table.setStyle(TableStyle([("FONTNAME", (0, 0), (-1, -1), "TestCyrillic"),
                                   ("FONTSIZE", (0, 0), (-1, -1), 10),
                                   ("GRID", (0, 0), (-1, -1), .5, "black")]))
        table.wrapOn(c, 500, 100)
        table.drawOn(c, 40, 660)
        c.save()
        return path

    def test_real_pdf_table_roundtrip(self):
        path = self.make_pdf()
        before = hashlib.sha256(path.read_bytes()).hexdigest()
        obs = parse_batch([path], ocr_mode="off", assessed_on=date(2026, 10, 3))
        self.assertEqual(len(obs.measurements), 2)
        hb, ferritin = obs.measurements
        self.assertEqual((hb.analyte, hb.value, hb.reference), ("hemoglobin", 130, "120 - 160"))
        self.assertEqual(hb.source.page, 1)
        self.assertEqual(hb.collected_on, "2026-10-01")
        self.assertEqual(ferritin.comparator, "<")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), before)
        self.assertEqual(obs.to_dict()["review_status"], "pending")

    def test_batch_different_dates_preserves_repeats(self):
        paths = [self.make_pdf("a.pdf", "01.09.2026", "110"), self.make_pdf("b.pdf", "01.10.2026", "130")]
        obs = parse_batch(paths, ocr_mode="off", assessed_on=date(2026, 10, 3))
        hb = [m for m in obs.measurements if m.analyte == "hemoglobin"]
        self.assertEqual(len(obs.documents), 2)
        self.assertEqual([m.value for m in hb], [110, 130])
        self.assertEqual([m.collected_on for m in hb], ["2026-09-01", "2026-10-01"])
        self.assertIn("repeated_analytes_preserved", obs.issues)

    def test_duplicate_documents_and_patient_mismatch(self):
        first = self.make_pdf("a.pdf")
        second = self.make_pdf("b.pdf", patient="Другой Пациент")
        obs = parse_batch([first, first, second], ocr_mode="off")
        self.assertIn("duplicate_documents_in_upload", obs.issues)
        self.assertIn("possible_patient_mismatch", obs.issues)

    def test_scan_reports_missing_ocr(self):
        from PIL import Image
        from reportlab.lib.utils import ImageReader
        path = self.root / "scan.pdf"
        c = canvas.Canvas(str(path))
        c.drawImage(ImageReader(Image.new("RGB", (800, 1100), "white")), 0, 0, width=595, height=842)
        c.save()
        obs = parse_batch([path], ocr_mode="off")
        self.assertIn("ocr_required", obs.documents[0].pages[0].issues)
        self.assertEqual(obs.measurements, [])

    def test_ocr_failure_keeps_batch_alive(self):
        path = self.make_pdf()
        with patch("lab_parser.extraction.MacOSOCR.extract", side_effect=RuntimeError("test failure")):
            obs = parse_batch([path], ocr_mode="always")
        self.assertTrue(any("ocr_failed" in issue for issue in obs.documents[0].pages[0].issues))
        self.assertEqual(len(obs.measurements), 2)

    def test_corrupt_pdf_and_page_limit(self):
        path = self.root / "broken.pdf"
        path.write_bytes(b"%PDF-1.7 broken")
        obs = parse_batch([path], ocr_mode="off")
        self.assertTrue(any("pdf_read_failed" in s for s in obs.documents[0].issues))
        good = self.make_pdf("good.pdf")
        c = canvas.Canvas(str(self.root / "two.pdf"))
        c.drawString(40, 700, "first page")
        c.showPage()
        c.drawString(40, 700, "second page")
        c.save()
        obs = parse_batch([good, self.root / "two.pdf"], ocr_mode="off", max_pages=1)
        self.assertEqual(len(obs.measurements), 2)
        self.assertIn("page_limit_exceeded:2", obs.documents[1].issues)

    def test_cli_writes_json_and_rejects_input_overwrite(self):
        path = self.make_pdf()
        output = self.root / "observation.json"
        self.assertEqual(main([str(path), "--ocr", "off", "-o", str(output)]), 0)
        self.assertTrue(output.exists())
        self.assertEqual(main([str(path), "-o", str(path)]), 1)

    def test_unruled_pdf_columns_follow_header_order(self):
        path = self.root / "unruled.pdf"
        c = canvas.Canvas(str(path))
        c.setFont("TestCyrillic", 10)
        for x, text in [(40, "Показатель"), (260, "Норма"), (380, "Результат"), (480, "Единицы")]:
            c.drawString(x, 760, text)
        for x, text in [(40, "Гемоглобин"), (260, "120 - 160"), (380, "118"), (480, "г/л")]:
            c.drawString(x, 740, text)
        c.save()
        obs = parse_batch([path], ocr_mode="off")
        self.assertEqual(len(obs.measurements), 1)
        self.assertEqual(obs.measurements[0].value, 118)
        self.assertEqual(obs.measurements[0].reference, "120 - 160")

    def test_invalid_policy_is_not_silently_replaced(self):
        with self.assertRaises(ValueError):
            parse_batch([self.make_pdf()], freshness_policy=[], ocr_mode="off")


class PublicSampleRegressionTests(unittest.TestCase):
    def test_official_helix_sample_values(self):
        # Optional downloaded fixture. Expected values were read from rendered
        # source pages, not copied from the parser's JSON output.
        path = Path(__file__).resolve().parents[1] / "tmp/public_samples/helix_checkup.pdf"
        if not path.exists():
            self.skipTest("Official Helix sample has not been downloaded")
        self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(),
                         "f04afcd254153c43792f296eb48b7a3e9d02434d116730a34112c4545bd53b16")
        obs = parse_batch([path], ocr_mode="off", assessed_on=date(2026, 10, 3))
        expected = {"hemoglobin": (154, "g/L"), "RBC": (4.87, "10^12/L"),
                    "WBC": (4.92, "10^9/L"), "hematocrit": (47.1, "%"), "MCV": (96.7, "fL"),
                    "MCH": (31.6, "pg"), "MCHC": (327, "g/L"), "RDW": (12.9, "%"),
                    "platelets": (296, "10^9/L"), "ESR": (5, "mm/h"),
                    "creatinine": (91.39, "µmol/L"), "eGFR": (94.57, "mL/min/1.73m²"),
                    "indirect_bilirubin": (6.73, "µmol/L"), "TSH": (2.5, "µIU/mL")}
        for analyte, value_unit in expected.items():
            candidates = [m for m in obs.measurements if m.analyte == analyte]
            self.assertEqual(len(candidates), 1, analyte)
            self.assertEqual((candidates[0].value, candidates[0].unit), value_unit, analyte)
        self.assertFalse(any(m.source.page == 1 and m.analyte for m in obs.measurements))
        self.assertFalse(any(m.source.page > 11 for m in obs.measurements))


if __name__ == "__main__":
    unittest.main()
