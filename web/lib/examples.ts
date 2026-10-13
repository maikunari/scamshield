/**
 * "Try an example" messages, so a judge never has to type. They are copies of three seed messages
 * (seed/messages.jsonl ids m01, m02, m17) and use only reserved 555-01xx numbers and example.com hosts.
 */
export interface Example {
  id: string;
  seedId: string;
  title: string;
  sender: string;
  body: string;
}

export const EXAMPLES: Example[] = [
  {
    id: "grandchild",
    seedId: "m01",
    title: "“Grandma, I’m in trouble”",
    sender: "212-555-0142",
    body:
      "Grandma it's me, I'm in big trouble. I had a car accident and I'm at the police station. I need $3,000 for bail today. Please don't tell Mom or Dad, I'm so embarrassed. Can you buy gift cards and send me the codes?",
  },
  {
    id: "parcel",
    seedId: "m02",
    title: "Parcel redelivery fee",
    sender: "212-555-0187",
    body:
      "ParcelPost: Your package could not be delivered. A redelivery fee of $1.99 is required. Pay within 24 hours: http://parcelpost-redelivery.example.com/pay",
  },
  {
    id: "taro",
    seedId: "m17",
    title: "Text from grandson Taro",
    sender: "212-555-0102",
    body: "Grandma it's Taro! The soccer game moved to Saturday at 2pm. Can you come? Love you!",
  },
];
