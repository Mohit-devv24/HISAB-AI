import csv
import io

def export_transactions_to_csv(transactions) -> str:
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(["ID", "Customer", "Email", "Type", "Amount (INR)", "Date"])
    for t in transactions:
        writer.writerow([t.id, t.customer_name, t.customer_email or "", t.type.value, t.amount_paise / 100.0, str(t.created_at)])
    return output.getvalue()
