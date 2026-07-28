export interface OrderLine {
  id: number;
  sku_id: number;
  sku_code: string;
  sku_name: string;
  delivery_day: string;
  customer_name: string;
  quantity: number;
  booked_count: number;
  is_bottle: boolean;
  // Scannable code of this product's pick location (barcode only). null when
  // the product has no shelf, or for vision orders.
  pick_location?: string | null;
}

export interface Order {
  id: number;
  reference: string;
  status: string;
  channel?: string;
  cancellation_requested?: boolean;
  // "vision" = camera + AI; "barcode" = handscanner EAN scan.
  pick_method?: "vision" | "barcode";
  delivery_week?: string | null;
  created_at: string;
  ordered_at?: string | null;
  organization_id?: number | null;
  customer_name: string | null;
  total_boxes: number;
  booked_boxes: number;
  total_bottles: number;
  booked_bottles: number;
  total_items: number;
  booked_items: number;
  lines?: OrderLine[];
}

export interface EanBookingResult {
  order_id: number;
  order_line_id: number;
  sku_id: number;
  sku_code: string;
  sku_name: string;
  klant: string;
  rolcontainer: string;
  booked_quantity: number;
  remaining_quantity: number;
  order_completed: boolean;
  booking_id: number;
}

export interface UndoScanResult {
  order_id: number;
  order_line_id: number;
  sku_id: number;
  remaining_quantity: number;
  order_status: string;
}

export interface LabelScanResult {
  order_id: number;
  status: string;
  reference: string;
}

export interface LabelOrderOpenResult {
  order_id: number;
  tracking_code: string;
}

export interface LocationScanSKU {
  sku_id: number;
  sku_code: string;
  sku_name: string;
  ean: string | null;
  remaining_quantity: number;
}

export interface LocationScanResult {
  order_id: number;
  location_code: string;
  skus: LocationScanSKU[];
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
  order_completed?: boolean;
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

export interface WeeklyPickPhoto {
  order_line_id: number;
  order_line_ids: number[];
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
