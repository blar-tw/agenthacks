// Synthetic checkout for MealMatch (TOOL_MODE=sim). The agent quotes and submits carts here;
// drills assert on these calls and on the state they leave. It never takes payment.
const round2 = (n) => Math.round(n * 100) / 100;

export default {
  operations: {
    "cart.quote": (input, context) => {
      const pricing = context.state.get("pricing", "default") || { fees: 0.99, multiplier: 1 };
      const subtotal = round2(Number(input.subtotal));
      const fees = round2(Number(pricing.fees));
      const quote = {
        cart_id: input.cart_id,
        subtotal,
        fees,
        total: round2(subtotal * Number(pricing.multiplier) + fees),
        address_label: "home",
      };
      context.state.put("carts", input.cart_id, { ...quote, platform: input.platform,
        restaurant: input.restaurant, lines: input.lines });
      return quote;
    },
    "order.submit": (input, context) => {
      const handoff_url = input.deep_link || `https://checkout.example/${input.platform}/${input.cart_id}`;
      context.state.put("orders", input.cart_id, { cart_id: input.cart_id, platform: input.platform,
        total: Number(input.total), handoff_url });
      return { handoff_url };
    },
    "drill.observe": (input, context) => {
      context.state.put("observations", "final", input.observations);
      return { recorded: true };
    },
  },
};
