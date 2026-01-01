// Item - Display Marketplace Listings Tab
// Renders read-only table of latest marketplace listings

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (!frm.is_new()) {
			load_marketplace_listings_tab(frm);
		}
	},
});

function load_marketplace_listings_tab(frm) {
	// Clear existing content
	if (frm.fields_dict.custom_marketplace_listings_tab) {
		frm.fields_dict.custom_marketplace_listings_tab.$wrapper.empty();
	}

	// Call API to get latest listings
	frappe
		.call({
			method: "electro_zone.electro_zone.doctype.marketplace_listing.marketplace_listing.get_latest_marketplace_listings",
			args: {
				item_code: frm.doc.name,
			},
		})
		.then(({ message }) => {
			if (!message || !message.success) {
				render_error_state(frm);
				return;
			}

			const listings = message.listings || [];
			render_listings_table(frm, listings);
		});
}

function render_listings_table(frm, listings) {
	const $wrapper = frm.fields_dict.custom_marketplace_listings_tab.$wrapper;

	let html = `
        <div class="marketplace-listings-display" style="padding: 10px;">
            <div style="margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">
                <h5 style="margin: 0;">Latest Marketplace Listings</h5>
                <button class="btn btn-sm btn-primary" onclick="create_new_marketplace_listing('${
					frm.doc.name
				}', '${frm.doc.item_name || ""}')">
                    <i class="fa fa-plus"></i> Create New Marketplace Listing
                </button>
            </div>
    `;

	if (listings.length === 0) {
		html += `
            <div class="alert alert-info" style="margin: 0;">
                <i class="fa fa-info-circle"></i> No marketplace listings found for this item.
                <br><small>Click "Create New Marketplace Listing" to add listings for Amazon, Noon, Jumia, etc.</small>
            </div>
        `;
	} else {
		html += `
            <table class="table table-bordered table-hover" style="margin: 0;">
                <thead style="background-color: #f5f7fa;">
                    <tr>
                        <th style="width: 15%;">Platform</th>
                        <th style="width: 20%;">ASIN / SKU</th>
                        <th style="width: 15%;">Commission</th>
                        <th style="width: 12%;">Shipping Fee</th>
                        <th style="width: 10%;">Status</th>
                        <th style="width: 13%;">Effective Date</th>
                        <th style="width: 15%;">Actions</th>
                    </tr>
                </thead>
                <tbody>
        `;
		listings.forEach((listing) => {
			const commission_display = listing.commission
				? frappe.format(listing.commission, { fieldtype: "Percent" })
				: "-";
			const shipping_display = listing.shipping_fee
				? frappe.format(listing.shipping_fee, { fieldtype: "Currency" })
				: "-";
			const status_color =
				listing.status === "Active"
					? "green"
					: listing.status === "Inactive"
					? "red"
					: "orange";

			html += `
                <tr>
                    <td><strong>${listing.platform}</strong></td>
                    <td style="font-family: monospace;">${listing.asin}</td>
                    <td>${commission_display}</td>
                    <td>${shipping_display}</td>
                    <td><span class="indicator-pill ${status_color}">${listing.status}</span></td>
                    <td>${frappe.datetime.str_to_user(listing.effective_date)}</td>
                    <td>
                        <button class="btn btn-xs btn-default" onclick="view_marketplace_listing('${
							listing.listing_name
						}')">
                            <i class="fa fa-eye"></i> View
                        </button>
            `;

			if (listing.listing_url) {
				html += `
                        <a href="${listing.listing_url}" target="_blank" class="btn btn-xs btn-default" style="margin-left: 5px;">
                            <i class="fa fa-external-link"></i>
                        </a>
                `;
			}

			html += `
                    </td>
                </tr>
            `;
		});

		html += `
                </tbody>
            </table>
            <div style="margin-top: 10px; font-size: 12px; color: #6c757d;">
                <i class="fa fa-info-circle"></i> Showing ${listings.length} latest listing(s).
                If multiple listings exist for the same Platform+ASIN, only the most recent is displayed.
            </div>
        `;
	}

	html += "</div>";

	$wrapper.html(html);
}

function render_error_state(frm) {
	const $wrapper = frm.fields_dict.custom_marketplace_listings_tab.$wrapper;
	$wrapper.html(`
        <div class="alert alert-warning" style="margin: 10px;">
            <i class="fa fa-exclamation-triangle"></i> Unable to load marketplace listings. Please refresh the page.
        </div>
    `);
}

// Global functions for button actions
window.create_new_marketplace_listing = function (item_code, item_name) {
	frappe.new_doc("Marketplace Listing", {
		item_code: item_code,
		item_name: item_name,
	});
};

window.view_marketplace_listing = function (listing_name) {
	frappe.set_route("Form", "Marketplace Listing", listing_name);
};
