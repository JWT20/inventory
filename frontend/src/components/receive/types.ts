export interface OrderLine {
  delivery_day: string;
  customer_name: string;
  quantity: number;
}

export interface Order {
  id: number;
  reference: string;
  status: string;
  delivery_week?: string | null;
  organization_id?: number | null;
  customer_name: string | null;
  total_boxes: number;
  booked_boxes: number;
  total_bottles: number;
  booked_bottles: number;
  lines?: OrderLine[];
}

export type ScanMode = "box" | "bottle";

export interface BookingResult {
  id: number;
  order_id: number;
  order_line_id?: number;
  order_reference: string;
  context_order_id?: number | null;
  context_order_reference?: string | null;
  sku_id?: number;
  sku_code: string;
  sku_name: string;
  klant: string;
  rolcontainer: string;
  needs_confirmation?: boolean;
  scan_image_url?: string;
  reference_image_urls?: string[];
  confidence?: number;
  booked_quantity?: number;
  remaining_quantity?: number;
}

export interface AlternativeMatch {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  confidence: number;
  reference_image_url: string;
  reference_image_urls?: string[];
  confirmation_token: string;
}

export interface DistributionLine {
  order_id: number;
  order_line_id: number;
  customer_name: string;
  rolcontainer: string;
  delivery_day: string;
  delivery_week?: string | null;
  ordered_quantity: number;
  booked_count: number;
  remaining_quantity: number;
  is_complete: boolean;
  is_context_order: boolean;
}

export interface DistributionResult {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  scope: string;
  total_remaining: number;
  lines: DistributionLine[];
}

export interface ConfirmationData {
  needs_confirmation: true;
  confirmation_token: string;
  order_id?: number;
  order_line_id?: number;
  order_reference?: string;
  context_order_id?: number | null;
  context_order_reference?: string | null;
  sku_code: string;
  sku_name: string;
  confidence: number;
  klant?: string;
  rolcontainer?: string;
  scan_image_url: string;
  reference_image_url: string;
  reference_image_urls?: string[];
  alternatives?: AlternativeMatch[];
  remaining_quantity?: number;
  cap_for_customer?: number | null;
  ordered_by_customer?: number | null;
}

export interface IdentifyResult {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  confidence: number;
  needs_confirmation: boolean;
  confirmation_reason: string | null;
  alternatives?: AlternativeMatch[];
  scan_image_url?: string;
  reference_image_urls?: string[];
}

export interface WeeklyPickPhoto {
  order_line_id: number;
  sku_id: number;
  wine_name: string;
  image_url: string | null;
  quantity: number;
  booked_count: number;
  customers: string[];
}

export interface NextPick {
  sku_id: number;
  sku_name: string;
  order_line_id: number;
  image_url: string | null;
  remaining_quantity: number;
  source: "this_order" | "other_order";
  order_id: number;
  customer_name: string | null;
}
