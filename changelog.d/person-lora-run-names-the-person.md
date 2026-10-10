- A run that loads a LoRA attached to a person now links the pictures it makes
  to that person, once their faces have been read. A run loading LoRAs of two
  or more people links nobody, and a LoRA at strength zero does not count.
- Fixed: deleting a person left their LoRAs attached to them, so the next
  person you created could show up as the owner of those LoRAs.
