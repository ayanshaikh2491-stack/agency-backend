"""Client Store subsystem — Shopify-like product + settings management.

Each workspace owns a `{schema}__store_products` and `{schema}__store_settings`
collection in PocketBase (reached through the Supabase-compatible gateway).
The Website Agent consumes these products when it (re)builds a client's live
site, so clients can manage their own storefront without touching the agency.
"""
