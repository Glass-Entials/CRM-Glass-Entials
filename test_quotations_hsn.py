import os
os.environ["FLASK_ENV"] = "testing"

from app import app
from model import db, QuotationItem
from routes.quotations import _calc_totals

def test():
    print("Running quotation tests...")
    
    # 1. Test quotation item creation WITH HSN/SAC
    item1 = QuotationItem(
        quotation_id=1,
        item_name="Item 1",
        rate=100.0,
        quantity=2.0,
        hsn_sac="8517"
    )
    assert item1.hsn_sac == "8517", "Failed to set HSN/SAC"
    print("✅ Creating quotation with HSN/SAC passed")
    
    # 2. Test quotation item creation WITHOUT HSN/SAC
    item2 = QuotationItem(
        quotation_id=1,
        item_name="Item 2",
        rate=50.0,
        quantity=1.0
    )
    assert item2.hsn_sac is None, "HSN/SAC should default to None"
    print("✅ Creating quotation without HSN/SAC passed")
    
    # 3. Test calculation logic (mocking the form data)
    items_data = [
        {
            "sort_order": 1,
            "item_name": "Test item",
            "description": "Desc",
            "width": 10.0,
            "height": 10.0,
            "quantity": 1.0,
            "unit": "Sq.Ft",
            "rate": 100.0,
            "discount": 0.0,
            "discount_type": "flat",
            "gst_percentage": 18.0,
            "hsn_sac": "9999"
        }
    ]
    totals = _calc_totals(items_data, 0, "flat", 0, False, False)
    
    assert len(totals["items"]) == 1
    assert totals["items"][0]["hsn_sac"] == "9999", "HSN/SAC not preserved in calculation"
    print("✅ Quotation calculation with HSN/SAC passed")
    
    print("All tests passed!")

if __name__ == "__main__":
    with app.app_context():
        test()
