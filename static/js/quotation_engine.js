/**
 * GlassEntials Enterprise Quotation Engine
 * Handles dynamic line item calculations, drag-n-drop, custom fields toggles, saving draft, and signature pad.
 */

document.addEventListener('DOMContentLoaded', () => {

// Configuration passed from template
const CONFIG = window.QE_CONFIG || { calcUrl: '', defaultGst: 18, isIgst: false, mode: 'create' };

// Elements
const form = document.getElementById('quotation-main-form') || document.getElementById('invoice-main-form');
const itemsBody = document.getElementById('items-tbody');
const templateRow = document.getElementById('item-row-template');
const btnAddRow = document.getElementById('btn-add-row');

// Custom fields logic
const btnToggleCf = document.getElementById('toggle-custom-fields');
const cfBody = document.getElementById('custom-fields-body');
const addCfDropdown = document.getElementById('add-custom-field-dropdown');

if (btnToggleCf && cfBody) {
  btnToggleCf.onclick = function() {
    const isHidden = cfBody.classList.toggle('hidden');
    this.textContent = isHidden ? 'Show Fields ▾' : 'Hide Fields ▴';
  };
}

if (addCfDropdown) {
  addCfDropdown.onchange = function() {
    const fieldKey = this.value;
    if (!fieldKey) return;
    
    console.log("Adding field:", fieldKey);
    const wrap = document.querySelector(`.qf-custom-field-wrap[data-field-key="${fieldKey}"]`);
    if (wrap) {
      wrap.classList.remove('cf-hidden');
      if (cfBody && cfBody.classList.contains('hidden')) {
        cfBody.classList.remove('hidden');
        if (btnToggleCf) btnToggleCf.textContent = 'Hide Fields ▴';
      }
    }
    this.value = "";
    const opt = this.querySelector(`option[value="${fieldKey}"]`);
    if (opt) opt.remove();
  };
}

// ─────────────────────────────────────────────────────────
// Customer Autofill
// ─────────────────────────────────────────────────────────
const custSelect = document.getElementById('customer-select');
const leadSelect = document.getElementById('lead-select');
const autofillPanel = document.getElementById('customer-autofill');

if (custSelect) {
  custSelect.addEventListener('change', function() {
    if (this.value) {
      if(leadSelect) leadSelect.value = ''; // clear lead
      const opt = this.options[this.selectedIndex];
      
      document.getElementById('af-company').textContent = opt.dataset.company || '—';
      document.getElementById('af-address').textContent = opt.dataset.address || '—';
      document.getElementById('af-gstin').textContent = opt.dataset.gstin || '—';
      document.getElementById('af-phone').textContent = opt.dataset.phone || '—';
      document.getElementById('af-email').textContent = opt.dataset.email || '—';
      document.getElementById('af-state').textContent = opt.dataset.state || '—';
      
      autofillPanel.style.display = 'block';
    } else {
      autofillPanel.style.display = 'none';
    }
  });
}

if (leadSelect) {
  leadSelect.addEventListener('change', function() {
    if (this.value && custSelect) {
      custSelect.value = '';
      autofillPanel.style.display = 'none';
    }
  });
}

// Valid till toggles
const vtType = document.getElementById('valid-till-type');
if (vtType) {
  vtType.addEventListener('change', function() {
    const dp = document.getElementById('due-date-picker');
    const dyp = document.getElementById('due-days-picker');
    dp.classList.add('hidden');
    dyp.classList.add('hidden');
    if(this.value === 'date') dp.classList.remove('hidden');
    if(this.value === 'days') dyp.classList.remove('hidden');
  });
}


// ─────────────────────────────────────────────────────────
// Line Items Engine (ERP)
// ─────────────────────────────────────────────────────────

function attachRowListeners(row) {
  row.querySelectorAll('.item-calc, .select-unit').forEach(el => {
    el.addEventListener('input', () => {
      calculateRowArea(row);
      scheduleRecalculate();
    });
    el.addEventListener('change', () => {
      calculateRowArea(row);
      scheduleRecalculate();
    });
  });
  
  row.querySelector('.btn-remove-erp')?.addEventListener('click', () => {
    row.remove();
    scheduleRecalculate();
  });
  
  row.querySelector('.btn-copy-erp')?.addEventListener('click', () => {
    const clone = row.cloneNode(true);
    // clean up values
    clone.querySelector('.btn-remove-erp').addEventListener('click', () => {
      clone.remove(); scheduleRecalculate();
    });
    clone.querySelectorAll('.item-calc, .select-unit').forEach(el => {
      el.addEventListener('input', () => { calculateRowArea(clone); scheduleRecalculate(); });
      el.addEventListener('change', () => { calculateRowArea(clone); scheduleRecalculate(); });
    });
    clone.querySelector('.btn-copy-erp').addEventListener('click', () => { /* basic copy hook */ });
    row.after(clone);
    updateRowSerials();
    scheduleRecalculate();
  });
}

function updateRowSerials() {
  document.querySelectorAll('#items-tbody .erp-item-row').forEach((row, i) => {
    const sn = row.querySelector('.col-sn');
    if(sn) sn.textContent = i + 1;
    row.dataset.row = i;
  });
}

function calculateRowArea(row) {
  const w = parseFloat(row.querySelector('.dim-w').value) || 0;
  const h = parseFloat(row.querySelector('.dim-h').value) || 0;
  const unit = row.querySelector('.select-unit')?.value;
  const areaVal = row.querySelector('.area-val');
  const cQtyInput = row.querySelector('.item-c-qty');
  let formula = row.querySelector('.input-formula')?.value || 'standard';

  let area = 0;
  
  if (['Sq.Ft', 'Sq.M'].includes(unit)) {
    const dimUnit = document.getElementById('global-dim-unit')?.value || 'Inches';
    let rawArea = w * h;
    let areaSqFt = rawArea;

    // First convert raw input area into Sq.Ft
    if (dimUnit === 'Inches') {
       areaSqFt = rawArea / 144.0;
    } else if (dimUnit === 'cm') {
       areaSqFt = rawArea / 929.0304;
    } else if (dimUnit === 'mm') {
       areaSqFt = rawArea / 92903.04;
    } // if Feet, it's already Sq.Ft equivalent

    // Now convert Sq.Ft to target billing unit, if necessary
    if (unit === 'Sq.M') {
        area = areaSqFt / 10.76391;
    } else {
        area = areaSqFt;
    }
    
    // Apply special rounding formulas if chosen
    if (formula === 'round_05') {
        area = Math.ceil(area * 2) / 2;
    } else if (formula === 'minimal_1') {
        area = Math.max(1.0, area);
    }

    if (areaVal) areaVal.textContent = area.toFixed(2);
    // Auto-fill chargeable quantity
    if (area > 0 && !cQtyInput.dataset.manual) {
      cQtyInput.value = area.toFixed(2);
    }
  } else {
    // Other units like Pcs, Nos, RFT
    if (areaVal) areaVal.textContent = '—';
    if (!cQtyInput.dataset.manual) {
      // Typically for Pieces/Nos, Chargeable Qty matches the pure quantity
      const qty = parseFloat(row.querySelector('.item-qty').value) || 1;
      cQtyInput.value = qty.toFixed(2);
    }
  }
}

// Ensure changing global unit triggers recalculation
const globalDimUnit = document.getElementById('global-dim-unit');
if (globalDimUnit) {
  globalDimUnit.addEventListener('change', () => {
    document.querySelectorAll('.erp-item-row').forEach(r => calculateRowArea(r));
    scheduleRecalculate();
  });
}

// Attach listeners to existing rows (edit mode)
document.querySelectorAll('.erp-item-row').forEach(row => attachRowListeners(row));

document.querySelectorAll('.btn-remove-group').forEach(btn => {
  btn.addEventListener('click', (e) => {
    e.target.closest('tr').remove();
  });
});

if (btnAddRow) {
  btnAddRow.addEventListener('click', () => {
    if (!templateRow) return;
    const clone = templateRow.content.cloneNode(true);
    const tr = clone.querySelector('tr');
    tr.dataset.row = Date.now();
    
    // Set default GST from config
    const gstSelect = tr.querySelector('.item-gst');
    if (gstSelect) gstSelect.value = CONFIG.defaultGst;
    
    attachRowListeners(tr);
    itemsBody.appendChild(tr);
    updateRowSerials();
  });
}

const btnAddGroup = document.getElementById('btn-add-group');
if (btnAddGroup) {
  btnAddGroup.addEventListener('click', () => {
    const tr = document.createElement('tr');
    tr.className = 'erp-group-row';
    tr.innerHTML = `<td colspan="15">
       <input type="text" class="erp-group-input" name="group_name_holder[]" placeholder="Group Name e.g. Glass (Rows below will belong to this)">
       <button type="button" class="btn-remove-group">✕</button>
    </td>`;
    tr.querySelector('.btn-remove-group').addEventListener('click', () => {
      tr.remove();
    });
    tr.querySelector('.erp-group-input').addEventListener('input', function() {
      // Find all rows below this until next group and update their hidden group_name
      let next = tr.nextElementSibling;
      while(next && !next.classList.contains('erp-group-row')) {
        const inp = next.querySelector('.input-group-name');
        if(inp) inp.value = this.value;
        next = next.nextElementSibling;
      }
    });
    itemsBody.appendChild(tr);
  });
}

// Make rows sortable if Sortable JS loaded
if (typeof Sortable !== 'undefined' && itemsBody) {
  new Sortable(itemsBody, {
    handle: '.drag-handle',
    animation: 150,
    ghostClass: 'sortable-ghost',
    onEnd: () => {
      updateRowSerials();
      scheduleRecalculate();
    }
  });
}

// ─────────────────────────────────────────────────────────
// Totals Engine
// ─────────────────────────────────────────────────────────
let calcTimeout = null;

function scheduleRecalculate() {
  if (calcTimeout) clearTimeout(calcTimeout);
  calcTimeout = setTimeout(calculateTotals, 300); // debounce API calls
}

// Globals listeners
['total-discount', 'total-discount-type', 'additional-charges', 'charges-taxable', 'toggle-igst']
  .forEach(id => {
    const el = document.getElementById(id);
    if(el) {
      el.addEventListener('input', scheduleRecalculate);
      el.addEventListener('change', scheduleRecalculate);
    }
  });

async function calculateTotals() {
  if (!CONFIG.calcUrl) return;

  const payload = {
    is_igst: document.getElementById('toggle-igst')?.checked || false,
    total_discount: parseFloat(document.getElementById('total-discount')?.value || 0),
    total_discount_type: document.getElementById('total-discount-type')?.value || 'flat',
    additional_charges: parseFloat(document.getElementById('additional-charges')?.value || 0),
    additional_charges_taxable: document.getElementById('charges-taxable')?.checked || false,
    items: []
  };

  const rows = itemsBody.querySelectorAll('tr.erp-item-row');
  rows.forEach(tr => {
    payload.items.push({
      width: parseFloat(tr.querySelector('.dim-w')?.value) || null,
      height: parseFloat(tr.querySelector('.dim-h')?.value) || null,
      quantity: parseFloat(tr.querySelector('.item-qty')?.value) || 0,
      chargeable_quantity: tr.querySelector('.item-c-qty')?.value || null,
      rate: parseFloat(tr.querySelector('.item-rate')?.value) || 0,
      discount: parseFloat(tr.querySelector('.item-disc')?.value) || 0,
      discount_type: tr.querySelector('.disc-type')?.value || 'flat',
      gst_percentage: parseFloat(tr.querySelector('.item-gst')?.value) || 0
    });
  });

  try {
    const res = await fetch(CONFIG.calcUrl, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload)
    });
    const data = await res.json();
    if (!data.success) throw new Error(data.error);
    
    updateUI(data.result, rows);
  } catch (err) {
    console.error("Calculation failed:", err);
  }
}

function updateUI(res, rows) {
  // Update line amounts
  res.items.forEach((itemRes, idx) => {
    const row = rows[idx];
    if (row && itemRes) {
      row.querySelector('.item-amount').textContent = `₹${itemRes.amount.toFixed(2)}`;
      const tolstr = row.querySelector('.item-total-str');
      if(tolstr) tolstr.textContent = `₹${itemRes.total.toFixed(2)}`;
    }
  });

  // Update totals panel
  document.getElementById('t-subtotal').textContent = `₹${res.subtotal.toFixed(2)}`;
  document.getElementById('t-discount').textContent = `— ₹${res.total_discount.toFixed(2)}`;
  document.getElementById('t-taxable').textContent = `₹${(res.subtotal - res.total_discount).toFixed(2)}`;
  
  if (res.is_igst) {
    document.getElementById('row-igst').classList.remove('hidden');
    document.getElementById('row-sgst').classList.add('hidden');
    document.getElementById('row-cgst').classList.add('hidden');
    document.getElementById('t-igst').textContent = `₹${res.igst.toFixed(2)}`;
  } else {
    document.getElementById('row-igst').classList.add('hidden');
    document.getElementById('row-sgst').classList.remove('hidden');
    document.getElementById('row-cgst').classList.remove('hidden');
    document.getElementById('t-sgst').textContent = `₹${res.sgst.toFixed(2)}`;
    document.getElementById('t-cgst').textContent = `₹${res.cgst.toFixed(2)}`;
  }
  
  document.getElementById('t-charges').textContent = `₹${res.additional_charges.toFixed(2)}`;
  document.getElementById('t-total').textContent = `₹${res.total_amount.toFixed(2)}`;
  document.getElementById('t-qty').textContent = res.total_quantity;
  document.getElementById('t-words').textContent = res.words + ' Only';
}

// ─────────────────────────────────────────────────────────
// Signature pad logic
// ─────────────────────────────────────────────────────────

const canvas = document.getElementById('signature-pad');
let signaturePad = null;
if (canvas && typeof SignaturePad !== 'undefined') {
  signaturePad = new SignaturePad(canvas, { backgroundColor: 'rgb(255, 255, 255)' });
  
  document.getElementById('btn-clear-sig').addEventListener('click', () => {
    signaturePad.clear();
    document.getElementById('pad-data-input').value = '';
  });
  
  document.getElementById('btn-save-sig').addEventListener('click', () => {
    if (signaturePad.isEmpty()) {
      alert("Please provide a signature first.");
    } else {
      const dataURL = signaturePad.toDataURL();
      document.getElementById('pad-data-input').value = dataURL;
      alert("Signature captured!");
    }
  });
}

// Sig tabs
document.querySelectorAll('.sig-tab').forEach(tab => {
  tab.addEventListener('click', () => {
    document.querySelectorAll('.sig-tab').forEach(t => t.classList.remove('active'));
    document.querySelectorAll('.sig-tab-panel').forEach(p => p.classList.add('hidden'));
    
    tab.classList.add('active');
    const targ = tab.dataset.tab;
    document.getElementById(`sig-panel-${targ}`).classList.remove('hidden');
    document.getElementById('sig-type-input').value = targ;
  });
});

// ─────────────────────────────────────────────────────────
// Attachments Drag/Drop
// ─────────────────────────────────────────────────────────
const dropZone = document.getElementById('attachment-drop-zone');
const fileInput = document.getElementById('att-file-input');
const uploadPreview = document.getElementById('upload-preview');

if (dropZone && fileInput) {
  ['dragenter', 'dragover', 'dragleave', 'drop'].forEach(ev => {
    dropZone.addEventListener(ev, handleDrag, false);
  });

  function handleDrag(e) {
    e.preventDefault();
    e.stopPropagation();
    if(e.type === 'dragenter' || e.type === 'dragover') dropZone.classList.add('drag-active');
    else dropZone.classList.remove('drag-active');
    
    if(e.type === 'drop') {
      let files = e.dataTransfer.files;
      fileInput.files = files; // HTML5 standard feature
      renderFilePreview(files);
    }
  }

  fileInput.addEventListener('change', () => renderFilePreview(fileInput.files));

  function renderFilePreview(files) {
    uploadPreview.innerHTML = '';
    Array.from(files).forEach(f => {
      const el = document.createElement('div');
      el.className = 'upload-file-pill mb-xs text-sm text-primary';
      el.textContent = `➕ ${f.name} (${(f.size/1024).toFixed(1)} KB)`;
      uploadPreview.appendChild(el);
    });
  }
}

// Existing attachment removal via AJAX (in edit mode)
document.querySelectorAll('.btn-remove-att').forEach(btn => {
  btn.addEventListener('click', async () => {
    if(!confirm("Remove this attachment?")) return;
    const attId = btn.dataset.attId;
    btn.parentElement.style.opacity = 0.5;
    try {
      const res = await fetch(`/quotations/attachments/${attId}/delete`, {method: 'POST'});
      if(res.ok) btn.parentElement.remove();
    } catch(e) { console.error(e); }
  });
});


// Save draft via AJAX handling
const btnDraft = document.getElementById('btn-save-draft');
if (btnDraft && CONFIG.saveDraftUrl) {
  btnDraft.addEventListener('click', async () => {
    btnDraft.disabled = true;
    btnDraft.textContent = 'Saving...';
    
    const formData = new FormData(form);
    try {
      const res = await fetch(CONFIG.saveDraftUrl, {
        method: 'POST', body: formData
      });
      const js = await res.json();
      if(js.success) {
        btnDraft.textContent = '✅ Saved';
        setTimeout(() => { btnDraft.textContent = '💾 Save Draft'; btnDraft.disabled=false; }, 2000);
      }
    } catch(e) {
      alert("Error saving draft");
      btnDraft.textContent = '💾 Save Draft';
      btnDraft.disabled = false;
    }
  });
}

});
