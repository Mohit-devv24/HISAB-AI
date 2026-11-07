export interface User {
  id: number;
  email: string;
  full_name: string;
  shop_name: string;
  role: 'owner' | 'manager' | 'staff';
}

export interface Transaction {
  id: number;
  customer_name: string;
  customer_email?: string;
  amount_paise: number;
  amount: number;
  type: 'UDHAAR' | 'JAMA';
  notes?: string;
  created_at: string;
}

export interface PaymentClaim {
  id: number;
  customer_name: string;
  amount_paise: number;
  amount: number;
  upi_ref: string;
  status: 'pending' | 'approved' | 'rejected';
  created_at: string;
}
