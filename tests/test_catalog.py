from apps.catalog.models import Product, StockMovement

from .base import ApiTestCase


class ProductTests(ApiTestCase):
    def test_crud(self):
        created = self.client.post("/api/products/", {"name": "جديد", "price": 2000, "cost": 800})
        self.assertCode(created, 201)
        product_id = created.data["id"]
        self.assertEqual(created.data["on_hand"], 0)
        self.assertEqual(len(self.client.get("/api/products/").data), 3)
        self.assertEqual(self.client.get(f"/api/products/{product_id}/").data["margin"], 1200)
        self.assertEqual(self.client.patch(f"/api/products/{product_id}/", {"price": 2100}).data["price"], 2100)
        full = self.client.put(f"/api/products/{product_id}/", {"name": "جديد 2", "price": 2500, "cost": 900})
        self.assertCode(full, 200)
        self.assertCode(self.client.delete(f"/api/products/{product_id}/"), 204)
        self.assertFalse(Product.objects.filter(pk=product_id).exists())

    def test_on_hand_cannot_be_edited_directly(self):
        self.client.patch(f"/api/products/{self.product.pk}/", {"on_hand": 99999})
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 100)

    def test_product_with_orders_is_deactivated_not_deleted(self):
        self.new_order()
        self.assertCode(self.client.delete(f"/api/products/{self.product.pk}/"), 204)
        self.assertFalse(Product.objects.get(pk=self.product.pk).is_active)

    def test_cost_hidden_from_confirmation_staff(self):
        self.as_user(self.agent)
        response = self.client.get(f"/api/products/{self.product.pk}/")
        self.assertCode(response, 200)
        self.assertNotIn("cost", response.data)
        self.assertNotIn("margin", response.data)

    def test_cost_visible_to_stock_staff(self):
        self.as_user(self.stock_user)
        self.assertEqual(self.client.get(f"/api/products/{self.product.pk}/").data["cost"], 1400)

    def test_duplicate_name_rejected(self):
        self.assertCode(self.client.post("/api/products/", {"name": "المنتج الأول", "price": 1}), 400)

    def test_stock_levels_follow_order_lifecycle(self):
        url = f"/api/products/{self.product.pk}/"
        order = self.confirmed_order(items=[{"product": self.product.pk, "quantity": 5}])
        levels = self.client.get(url).data
        self.assertEqual((levels["on_hand"], levels["reserved"], levels["available"]), (100, 5, 95))
        self.client.post("/api/shipping/send/", {"order_ids": [order["id"]]})
        levels = self.client.get(url).data
        self.assertEqual((levels["on_hand"], levels["reserved"], levels["at_courier"], levels["available"]), (95, 0, 5, 95))
        self.assertEqual(levels["runway_days"], 570)

    def test_low_stock_state(self):
        self.client.post(f"/api/products/{self.product.pk}/adjust/", {"quantity": -95, "note": "جرد"})
        self.assertEqual(self.client.get(f"/api/products/{self.product.pk}/").data["state"], "low")
        self.client.post(f"/api/products/{self.product.pk}/adjust/", {"quantity": -5, "note": "جرد"})
        self.assertEqual(self.client.get(f"/api/products/{self.product.pk}/").data["state"], "out")


class StockMovementTests(ApiTestCase):
    def test_stock_in(self):
        response = self.client.post(f"/api/products/{self.product.pk}/stock-in/", {"quantity": 300, "note": "دفعة"})
        self.assertCode(response, 201)
        self.assertEqual(response.data["balance_after"], 400)
        self.assertEqual(response.data["kind_label"], "إدخال مخزون")
        self.assertCode(self.client.post(f"/api/products/{self.product.pk}/stock-in/", {"quantity": 0}), 400)

    def test_adjust(self):
        url = f"/api/products/{self.product.pk}/adjust/"
        self.assertEqual(self.client.post(url, {"quantity": -2, "note": "جرد"}).data["balance_after"], 98)
        self.assertCode(self.client.post(url, {"quantity": 0, "note": "جرد"}), 400)
        self.assertCode(self.client.post(url, {"quantity": 1}), 400)
        self.assertCode(self.client.post(url, {"quantity": -1000, "note": "جرد"}), 409, "insufficient_stock")
        self.assertEqual(Product.objects.get(pk=self.product.pk).on_hand, 98)

    def test_movement_history(self):
        self.client.post(f"/api/products/{self.product.pk}/stock-in/", {"quantity": 5})
        response = self.client.get("/api/stock-movements/", {"product": self.product.pk, "kind": "in"})
        self.assertCode(response, 200)
        self.assertEqual(response.data["count"], 2)
        self.assertEqual(response.data["results"][0]["quantity"], 5)
        self.assertEqual(StockMovement.objects.filter(product=self.product).count(), 2)
