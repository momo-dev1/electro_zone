// Item - Display Marketplace Listings Tab
// Renders read-only table of latest marketplace listings
// Item - Recalculate Price on Sellout Included Change

frappe.ui.form.on("Item", {
	refresh(frm) {
		if (!frm.is_new()) {
			load_marketplace_listings_tab(frm);
		}
	},

	custom_sellout_included(frm) {
		// Only recalculate if document exists (not new)
		if (!frm.is_new()) {
			recalculate_final_price_and_rebate(frm);
		} else {
			console.log("Skipped: Document is new");
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

	// Collect all URLs for "Open All Links" functionality
	const all_urls = listings.filter(l => l.listing_url).map(l => l.listing_url);
	const has_urls = all_urls.length > 0;

	let html = `
        <div class="marketplace-listings-display" style="padding: 10px;">
            <div style="margin-bottom: 15px; display: flex; justify-content: space-between; align-items: center;">
                <h5 style="margin: 0;">Latest Marketplace Listings</h5>
                <div>
                    ${has_urls ? `
                    <button class="btn btn-sm btn-default open-all-links-btn" style="margin-right: 10px;">
                        <i class="fa fa-external-link"></i> Open All Links
                    </button>
                    ` : ''}
                    <button class="btn btn-sm btn-primary" onclick="create_new_marketplace_listing('${
					frm.doc.name
				}', '${frm.doc.item_name || ""}')">
                        <i class="fa fa-plus"></i> Create New Marketplace Listing
                    </button>
                </div>
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
                        <th style="width: 14%;">Platform</th>
                        <th style="width: 18%;">ASIN / SKU</th>
                        <th style="width: 13%;">Commission</th>
                        <th style="width: 11%;">Shipping Fee</th>
                        <th style="width: 9%;">Status</th>
                        <th style="width: 12%;">Effective Date</th>
                        <th style="width: 8%;">Link</th>
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
                    <td style="text-align: center;">
            `;

			if (listing.listing_url) {
				html += `
                        <a href="${listing.listing_url}" target="_blank" class="btn btn-xs btn-default">
                            <i class="fa fa-external-link"></i>
                        </a>
                `;
			} else {
				html += `-`;
			}

			html += `
                    </td>
                    <td>
                        <button class="btn btn-xs btn-default" onclick="view_marketplace_listing('${
							listing.listing_name
						}')">
                            <i class="fa fa-eye"></i> View
                        </button>
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

	// Attach event handler for "Open All Links" button
	if (has_urls) {
		$wrapper.find('.open-all-links-btn').on('click', function() {
			open_all_marketplace_links(all_urls);
		});
	}
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

window.open_all_marketplace_links = function (urls) {
	if (!urls || urls.length === 0) {
		frappe.show_alert({
			message: __("No URLs to open"),
			indicator: "orange",
		});
		return;
	}

	// Open each URL in a new tab
	urls.forEach((url) => {
		window.open(url, "_blank");
	});

	frappe.show_alert({
		message: __(`Opened ${urls.length} link(s) in new tabs`),
		indicator: "green",
	});
};

// ============================================================================
// Item - Recalculate Price on Sellout Included Change (ERPNext v15.8+)
// Purpose:
// - When user changes the "Sellout Included" checkbox, immediately recalculate final price
// - ALWAYS call Rebate List recalculation API to update latest record
// - Provides instant feedback with detailed logging
//
// Business Rule:
// - Checked (1): Final Price = Price List - Promo - Sellout Promo (sellout INCLUDED)
// - Unchecked (0): Final Price = Price List - Promo (sellout EXCLUDED)
// ============================================================================

function recalculate_final_price_and_rebate(frm) {
	// Get current pricing data from Item fields
	let price_list = frm.doc.custom_current_final_price_list || 0;
	let promo = frm.doc.custom_current_final_promo || 0;
	let sellout = frm.doc.custom_current_final_sellout_promo || 0;
	let sellout_included = frm.doc.custom_sellout_included || 0;

	// Calculate final price based on checkbox state
	let final_price = 0;
	let calculation_note = "";

	if (sellout_included) {
		// Checked: Include sellout promo in calculation
		final_price = price_list - promo - sellout;
		calculation_note = `${price_list} - ${promo} - ${sellout} = ${final_price} (Sellout INCLUDED)`;
	} else {
		// Unchecked (default): Exclude sellout promo
		final_price = price_list - promo;
		calculation_note = `${price_list} - ${promo} = ${final_price} (Sellout EXCLUDED)`;
	}

	// Round to 2 decimal places for currency precision
	final_price = Math.round(final_price * 100) / 100;

	// Update the calculated field
	frm.set_value("custom_current_final_price_list_calculated", final_price);

	// Show user feedback
	frappe.show_alert({
		message:
			__("Final price recalculated: ") +
			final_price +
			" (" +
			(sellout_included ? "Sellout INCLUDED" : "Sellout EXCLUDED") +
			")",
		indicator: "green",
	});

	frm.save()
		.then(() => {
			// Now call the API to update Rebate List
			call_rebate_recalculation_api(frm);
		})
		.catch((error) => {
			frappe.show_alert({
				message: __("Failed to save Item. Rebate List not updated."),
				indicator: "red",
			});
		});
}

function call_rebate_recalculation_api(frm) {
	frappe.call({
		method: "electro_zone.electro_zone.doctype.rebate_list.rebate_list.recalculate_rebate_for_item",
		args: {
			item_code: frm.doc.name,
		},
		callback: function (r) {
			if (r.message && r.message.success) {
				if (r.message.updated_count > 0) {
					frappe.show_alert({
						message: __(
							`✅ Updated ${r.message.updated_count} Rebate List record(s) with new Final Price List: ${r.message.new_final_price_list}`
						),
						indicator: "green",
					});
					frm.reload_doc();
				} else {
					frappe.show_alert({
						message: __("No Rebate List records found to update."),
						indicator: "blue",
					});
				}
			} else if (r.message && !r.message.success) {
				frappe.show_alert({
					message: __(r.message.message || "Failed to update Rebate List records."),
					indicator: "orange",
				});
			} else {
			}
		},
		error: function (error) {
			frappe.show_alert({
				message: __("Error calling Rebate List recalculation API."),
				indicator: "red",
			});
		},
	});
}
