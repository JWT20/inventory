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
  // Code of this product's pick location. The EAN flow verifies it by scanning;
  // for a loose bottle it is only shown, because wine is matched by photo. null
  // when the product has no shelf — a whole wine box never has one.
  pick_location?: string | null;
}

export interface Order {
  id: number;
  reference: string;
  status: string;
  channel?: string;
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
  // Waar dit product ligt, als het aan een schap gekoppeld is.
  pick_location?: string | null;
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
  // False for a lookalike that is not open in this scan scope: shown with its
  // photo so the picker can recognise the box they are actually holding, but
  // it cannot be booked here.
  bookable?: boolean;
  note?: string;
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
  confirmation_reason?: string | null;
  manual_review_required?: boolean;
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
  // Waar het product ligt, als het aan een schap gekoppeld is. Alleen tonen:
  // wijn wordt op foto herkend, niet op een scan van het schap.
  pick_location?: string | null;
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
