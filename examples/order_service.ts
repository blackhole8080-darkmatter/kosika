/**
 * Order intake service.
 * Reads an order, calls the payment gateway, and records the result.
 */
import { readFile, writeFile } from "node:fs/promises";
import * as logger from "./logger.js";
import { retry } from "./util.js";

export interface Order {
  id: string;
  total: number;
  customer: string;
}

export type OrderResult = { ok: boolean; chargeId?: string };

export enum Status {
  Pending = "pending",
  Paid = "paid",
  Failed = "failed",
}

/** Charges a customer and persists the receipt. */
export async function chargeOrder(order: Order, gateway: any): Promise<OrderResult> {
  const receipt = await gateway.charge(order.total, order.id);
  await writeFile(`/receipts/${order.id}.json`, JSON.stringify(receipt));
  logger.info("charged", order.id);
  return { ok: true, chargeId: receipt.id };
}

const discounted = (order: Order): number => order.total * 0.9;

/** In-memory order store used by the demo. */
export class OrderStore {
  private orders: Map<string, Order> = new Map();

  add(order: Order): void {
    this.orders.set(order.id, order);
  }

  get size(): number {
    return this.orders.size;
  }
}

export default OrderStore;
