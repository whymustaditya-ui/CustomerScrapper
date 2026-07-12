/**
 * Beautify — CRM Leads (ROSH Customer Scraper).
 *
 * One-off styling for the leads tab, run straight from the Apps Script editor
 * bound to the CRM spreadsheet (Extensions → Apps Script). No Streamlit / Python
 * involved, so no server-restart dance. Idempotent — safe to run repeatedly.
 *
 * Matches the ROSH × Accurate visual system: dark INK header with a green accent
 * underline, zebra rows, colour-coded Status chips, a heat gradient on Skor, and
 * tuned column widths. It reads the header labels in row 1, so column order can
 * change without breaking anything.
 *
 * The leads tab is found automatically by its header signature ("Nama Bisnis"),
 * so the exact tab name (often truncated/renamed) doesn't matter.
 *
 * HOW TO RUN
 *   1. Open the CRM Leads Google Sheet (the one the scraper writes to).
 *   2. Extensions → Apps Script.
 *   3. Paste this file, save.
 *   4. Pick beautifyCrmLeads in the function dropdown → Run. Authorize once.
 */

var LEADS_TAB = 'CRM Leads (Scrapper)';  // fallback; deteksi via header menang duluan

// The header label that uniquely marks the leads tab. Used to auto-find the sheet
// so the exact tab name (which is often truncated/renamed) doesn't matter.
var LEADS_SIGNATURE = 'Nama Bisnis';

// Find the leads sheet by its header signature; fall back to LEADS_TAB by name.
function _findLeadsSheet(ss) {
  var sheets = ss.getSheets();
  for (var i = 0; i < sheets.length; i++) {
    var sh = sheets[i];
    var lc = sh.getLastColumn();
    if (lc < 1) continue;
    var hdr = sh.getRange(1, 1, 1, lc).getValues()[0]
      .map(function (h) { return String(h).trim(); });
    if (hdr.indexOf(LEADS_SIGNATURE) !== -1) return sh;
  }
  return ss.getSheetByName(LEADS_TAB) || null;
}

var LUI = {
  INK:   '#1f2937', WHITE: '#ffffff',
  BAND:  '#f3f4f6', NOTE:  '#6b7280', GREEN: '#2f7d4f',

  // Status chips: background + text colour per pipeline stage.
  STATUS: {
    'New':       { bg: '#e8eaed', fg: '#5f6368' },
    'Contacted': { bg: '#e1ecfb', fg: '#1967d2' },
    'Replied':   { bg: '#fef3d6', fg: '#b45309' },
    'Quoted':    { bg: '#f1e4fb', fg: '#8430ce' },
    'Won':       { bg: '#ddf0e3', fg: '#0f7a3d' },
    'Lost':      { bg: '#fbe0de', fg: '#c5221f' }
  },

  // Skor heat gradient (low → high).
  SCORE_MIN: '#f8d2d2', SCORE_MID: '#fff3cd', SCORE_MAX: '#c6e7d0',

  // Sensible pixel widths, keyed by the friendly header label.
  WIDTHS: {
    'Batch': 55, 'Tgl Masuk': 95, 'Status': 105, 'PIC': 70,
    'Tgl Follow-up': 105, 'Catatan': 170, 'Nama Bisnis': 250, 'Industri': 95,
    'Ukuran Usaha': 105, 'Skor': 60, 'No. WhatsApp': 135, 'Chat WA': 105,
    'Website': 170, 'Kelurahan': 120, 'Kota': 120, 'Alamat': 270,
    'ID Lokasi': 150, 'Lokasi Maps': 105
  }
};

function beautifyCrmLeads() {
  var ss = SpreadsheetApp.getActiveSpreadsheet();
  var sh = _findLeadsSheet(ss);
  if (!sh) {
    var names = ss.getSheets().map(function (s) { return s.getName(); }).join(', ');
    SpreadsheetApp.getActive().toast(
      'Tab lead tak ketemu (cari header "' + LEADS_SIGNATURE + '"). Tab yang ada: ' + names,
      'ROSH', 8);
    return;
  }

  var lastCol = sh.getLastColumn();
  if (lastCol < 1) {
    SpreadsheetApp.getActive().toast('Tab kosong — tidak ada yang diformat.', 'ROSH', 5);
    return;
  }
  var headers = sh.getRange(1, 1, 1, lastCol).getValues()[0]
    .map(function (h) { return String(h).trim(); });
  var endRow = Math.max(sh.getLastRow(), 1000);  // cover future appended rows too
  var nData = endRow - 1;

  function col(label) { var i = headers.indexOf(label); return i < 0 ? -1 : i + 1; }

  // 0. Reset prior formatting we own (idempotent re-runs).
  sh.clearConditionalFormatRules();
  sh.getBandings().forEach(function (b) { b.remove(); });

  // 1. Freeze the header row.
  sh.setFrozenRows(1);

  // 2. Header row: dark INK, white bold, wrapped, taller, green accent underline.
  var head = sh.getRange(1, 1, 1, lastCol);
  head.setBackground(LUI.INK).setFontColor(LUI.WHITE).setFontWeight('bold')
      .setVerticalAlignment('middle').setHorizontalAlignment('left').setWrap(true);
  sh.setRowHeight(1, 34);
  head.setBorder(null, null, true, null, null, null,
                 LUI.GREEN, SpreadsheetApp.BorderStyle.SOLID_THICK);

  // 3. Data rows: readable size, middle-aligned, zebra banding, comfy height.
  var data = sh.getRange(2, 1, nData, lastCol);
  data.setVerticalAlignment('middle').setFontSize(10);
  var banding = data.applyRowBanding(SpreadsheetApp.BandingTheme.LIGHT_GREY, false, false);
  banding.setFirstRowColor(LUI.WHITE).setSecondRowColor(LUI.BAND);
  for (var rr = 2; rr <= endRow; rr++) sh.setRowHeight(rr, 26);

  // 4. Column widths by label.
  Object.keys(LUI.WIDTHS).forEach(function (label) {
    var c = col(label);
    if (c > 0) sh.setColumnWidth(c, LUI.WIDTHS[label]);
  });

  var rules = [];

  // 5. Status: one coloured chip per stage, centered + bold.
  var sc = col('Status');
  if (sc > 0) {
    var srange = sh.getRange(2, sc, nData, 1);
    srange.setHorizontalAlignment('center');
    Object.keys(LUI.STATUS).forEach(function (name) {
      var c = LUI.STATUS[name];
      rules.push(SpreadsheetApp.newConditionalFormatRule()
        .whenTextEqualTo(name)
        .setBackground(c.bg).setFontColor(c.fg).setBold(true)
        .setRanges([srange]).build());
    });
  }

  // 6. Skor: 1-decimal, centered, red → amber → green heat gradient.
  var kc = col('Skor');
  if (kc > 0) {
    var krange = sh.getRange(2, kc, nData, 1);
    krange.setHorizontalAlignment('center').setNumberFormat('0.0');
    rules.push(SpreadsheetApp.newConditionalFormatRule()
      .setGradientMinpoint(LUI.SCORE_MIN)
      .setGradientMidpointWithValue(LUI.SCORE_MID,
        SpreadsheetApp.InterpolationType.PERCENTILE, '50')
      .setGradientMaxpoint(LUI.SCORE_MAX)
      .setRanges([krange]).build());
  }

  sh.setConditionalFormatRules(rules);
  SpreadsheetApp.getActive().toast('Tab "' + sh.getName() + '" dipercantik ✓', 'ROSH', 6);
}
