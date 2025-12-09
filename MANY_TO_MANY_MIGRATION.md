# Many-to-Many Character-Picture Relationship Migration

## Overview
Converting from one-to-many (Picture → Character) to many-to-many (Picture ↔ Characters) relationship to support pictures with multiple people.

**Key Decision:** We've renamed `character_id` → `primary_character_id` which allows us to maintain backward compatibility and implement features in three phases:

- **Phase 1:** Single character support via `primary_character_id` (✅ Complete)
- **Phase 2:** Multi-character UI and basic junction table support (Future)
- **Phase 3:** Advanced multi-character features - tagging, embeddings, and face detection for secondary characters (Future)

The `primary_character_id` represents the "main" character in a picture, while `character_ids` (from the junction table) can represent all people in group photos.

## Completed Changes

### Schema Changes ✅
1. **Created `picture_character.py`** - Junction table model
   - Fields: `picture_id` (str), `character_id` (int)
   - Composite primary key on both fields
   - Indexes on both fields for fast lookups

2. **Created `picture_characters.py`** - CRUD operations for junction table
   - `add(picture_id, character_id)` - Add association
   - `remove(picture_id, character_id)` - Remove association
   - `get_characters_for_picture(picture_id)` - Get all characters in a picture
   - `get_pictures_for_character(character_id)` - Get all pictures of a character
   - `set_characters_for_picture(picture_id, character_ids)` - Replace all associations
   - `clear_picture(picture_id)` - Remove all character associations for a picture
   - `clear_character(character_id)` - Remove picture when character is deleted

3. **Updated `picture.py` (PictureModel)**
   - ✅ Renamed: `character_id` → `primary_character_id` (with foreign key and index maintained)
   - ✅ Added: `character_ids` field (list[int], db_ignore=True) for many-to-many
   - Updated `to_dict()` - includes both `primary_character_id` and `character_ids`
   - Updated `from_dict()` - reads both fields
   - **Backward Compatible:** All existing code continues to work, just uses new field name

4. **Updated `database.py`**
   - Added `PictureCharacterModel` to models list
   - Junction table will be created on database initialization

5. **Updated `vault.py`**
   - Added import for `PictureCharacters`
   - Initialized `self.picture_characters` in constructor

6. **Updated all backend files** ✅
   - `server.py` - All endpoints updated to use `primary_character_id`
   - `pictures.py` - References updated to `primary_character_id`
   - `picture_utils.py` - Parameter usage updated
   - `cli.py` - **DELETED** (not used anymore)
   - `tests/test_server.py` - All test data updated

7. **Updated frontend** ✅
   - `App.vue` - All API calls updated to use `primary_character_id`

## Backend Changes Needed

### 1. Pictures CRUD (`pictures.py`) - DEFERRED TO PHASE 2

**Current Status:** 
- ✅ All references updated to use `primary_character_id` instead of `character_id`
- ⏸️ **Deferred:** Full many-to-many support (using junction table for filtering)
- **Why defer:** `primary_character_id` already works perfectly for current single-character workflow

**Phase 2 Updates (Future):**

**`find()` method:**
- Add optional parameter to search junction table for multi-character filtering
- Current: `WHERE primary_character_id = ?` (works fine for now)
- Future: Add option to use junction table:
  ```sql
  SELECT p.* FROM pictures p
  WHERE p.id IN (
    SELECT picture_id FROM picture_characters WHERE character_id = ?
  )
  ```

**`delete()` method:**
- Add call to `vault.picture_characters.clear_picture(picture_id)` to clean up associations

**Helper method (when needed):**
```python
def _populate_character_ids(self, pictures: list[PictureModel]):
    """Populate character_ids for a list of pictures."""
    for pic in pictures:
        pic.character_ids = self.vault.picture_characters.get_characters_for_picture(pic.id)
    return pictures
```

### 2. Characters CRUD (`characters.py`) - MOSTLY COMPLETE

**Current Status:**
- ✅ All references updated to use `primary_character_id`
- ⏸️ **Deferred:** Junction table cleanup on delete

**Phase 2 Updates (Future):**

**`delete()` method:**
- Add call to `vault.picture_characters.clear_character(character_id)` before deletion
- This will clean up many-to-many associations when we start using them

**Picture counting:**
- Currently uses `WHERE primary_character_id = ?` (works fine)
- Could optionally enhance to include junction table counts for multi-character pictures

### 3. Server API (`server.py`) - ✅ COMPLETE FOR PHASE 1

**Current Status:**
- ✅ All endpoints updated to use `primary_character_id`
- ✅ POST `/pictures` accepts `primary_character_id` parameter
- ✅ GET `/pictures` filters by `primary_character_id`
- ✅ GET `/picture_ids` filters by `primary_character_id`
- ✅ GET `/face_thumbnail/{primary_character_id}` updated
- ✅ GET `/category/summary` uses `primary_character_id`
- ✅ PATCH `/pictures/{id}` invalidates embeddings on `primary_character_id` change
- ✅ Search endpoint uses `primary_character_id` for character matching

**Phase 2 Enhancements (Future):**

**`POST /pictures/upload`**
- Accept optional `character_ids` array parameter for multi-character pictures
- Call `vault.picture_characters.set_characters_for_picture()` when provided

**`PATCH /pictures/{picture_id}`**
- Add support for updating `character_ids` array
- Call `vault.picture_characters.set_characters_for_picture()` for multi-character updates

**`GET /pictures` response:**
- Optionally populate `character_ids` field by querying junction table
- Useful when displaying all characters in a picture

### 4. Picture Tagger (`picture_tagger.py`) - ✅ COMPLETE FOR PHASE 1, PHASE 3 FOR ADVANCED

**Phase 1 Status (✅ Complete):**
- ✅ All references updated to use `primary_character_id`
- ✅ Face detection assigns to primary character (works as before)
- ✅ Character embeddings use primary character name (works as before)
- ✅ Tags filtered based on primary character

**Phase 2 (No tagger changes needed):**
- Phase 2 is purely about UI and basic junction table management
- Tagging/embedding/face detection continue to use primary character only
- Additional characters are for organization/filtering only

**Phase 3 Enhancements (Future - advanced multi-character features):**

**Multi-face detection:**
- Detect multiple faces in a single image
- Match faces to character reference photos
- Automatically populate junction table with detected secondary characters
- Store face bounding boxes per character (enhance junction table)
- Primary character = largest/most prominent face

**Multi-character tagging:**
- Generate separate tags for each detected character
- Store character-specific tags in enhanced junction table
- Example: "character1_tags: [blonde, smiling]", "character2_tags: [brunette, waving]"

**Multi-character embeddings:**
- Generate embeddings that include all character names in context
- Weight primary character name higher (e.g., 2x) than secondary characters
- Enable search like "Alice and Bob at the beach" to find group photos
- Store primary vs. secondary character distinction in embedding context

### 5. CLI (`cli.py`) - ✅ DELETED

**Status:** CLI is not used anymore and has been removed from the codebase to avoid maintenance overhead.

## Frontend Changes

### Phase 1: ✅ COMPLETE (Single Character via primary_character_id)

**Status:**
- ✅ All API calls updated to use `primary_character_id` parameter
- ✅ Filtering works with `primary_character_id`
- ✅ Character assignment uses `primary_character_id`
- ✅ All existing single-character workflows functional

### Phase 2: Multi-Character UI (Future - Basic Multi-Character Support)

**Scope:** Add UI for managing multiple characters per picture (primary + additional)
**Focus:** User can manually assign multiple characters, no automatic detection yet

#### 1. Data Model Updates

Add `character_ids` array to Picture interface (will come from backend):
```typescript
interface Picture {
  id: string;
  primary_character_id: number | null;  // Main character
  character_ids: number[];                // All characters (from junction table)
  ...
}
```

#### 2. ImageOverlay Component

Add character management UI with primary character distinction:
```vue
<div class="overlay-characters">
  <!-- Primary Character (highlighted/different style) -->
  <div class="primary-character-section">
    <label>Primary Character:</label>
    <select v-model="image.primary_character_id" @change="updatePrimaryCharacter(image)">
      <option :value="null">None</option>
      <option v-for="char in characters" :key="char.id" :value="char.id">
        {{ char.name }}
      </option>
    </select>
  </div>
  
  <!-- Additional Characters (from junction table) -->
  <div class="additional-characters-section">
    <label>Also in picture:</label>
    <div class="character-chips">
      <span 
        v-for="charId in image.character_ids.filter(id => id !== image.primary_character_id)" 
        :key="charId" 
        class="character-chip secondary"
      >
        {{ getCharacterName(charId) }}
        <button @click="removeCharacter(image, charId)" title="Remove from picture">×</button>
      </span>
      <button @click="showCharacterSelector(image)" class="add-character-btn">
        + Add Character
      </button>
    </div>
  </div>
</div>

<!-- Styling to distinguish primary vs secondary -->
<style scoped>
.primary-character-section {
  padding: 10px;
  background: rgba(100, 150, 255, 0.1);
  border-left: 3px solid #4a90e2;
  margin-bottom: 10px;
}

.additional-characters-section {
  padding: 10px;
}

.character-chip.secondary {
  background: rgba(150, 150, 150, 0.2);
  border: 1px solid rgba(150, 150, 150, 0.4);
}
</style>
```

**Key Features:**
- Primary character shown in separate section with dropdown selector
- Secondary characters shown as removable chips
- Visual distinction (color, border, layout)
- Can't remove primary character via chip (must change dropdown first)
- Primary character automatically included in `character_ids` list

#### 3. ImageImporter Component

Upload UI should clearly distinguish primary vs additional characters:
```vue
<div class="character-assignment">
  <!-- Primary Character Selection -->
  <div class="form-group">
    <label>Primary Character (main subject):</label>
    <select v-model="uploadForm.primary_character_id">
      <option :value="null">None</option>
      <option v-for="char in characters" :key="char.id" :value="char.id">
        {{ char.name }}
      </option>
    </select>
  </div>
  
  <!-- Additional Characters (optional) -->
  <div class="form-group">
    <label>Also in picture (optional):</label>
    <div class="multi-select-chips">
      <span 
        v-for="charId in uploadForm.additional_character_ids" 
        :key="charId"
        class="character-chip"
      >
        {{ getCharacterName(charId) }}
        <button @click="removeAdditionalCharacter(charId)">×</button>
      </span>
      <select 
        v-model="additionalCharacterToAdd" 
        @change="addAdditionalCharacter"
        :disabled="!uploadForm.primary_character_id"
      >
        <option value="">+ Add another character...</option>
        <option 
          v-for="char in availableAdditionalCharacters" 
          :key="char.id" 
          :value="char.id"
        >
          {{ char.name }}
        </option>
      </select>
    </div>
    <small class="help-text">Select primary character first</small>
  </div>
</div>
```

**Key Features:**
- Primary character selection is required/emphasized
- Additional characters are optional and clearly labeled
- Can't add additional characters until primary is selected
- Dropdown filters out primary character from additional options

#### 4. Image Thumbnails

Show character indicators with primary character emphasized:
```vue
<div class="image-character-indicators">
  <!-- Primary character badge (larger/highlighted) -->
  <div 
    v-if="image.primary_character_id" 
    class="character-badge primary"
    :title="`Primary: ${getCharacterName(image.primary_character_id)}`"
  >
    {{ getCharacterInitial(image.primary_character_id) }}
  </div>
  
  <!-- Count of additional characters -->
  <div 
    v-if="additionalCharacterCount(image) > 0" 
    class="character-badge secondary-count"
    :title="`+ ${additionalCharacterCount(image)} more`"
  >
    +{{ additionalCharacterCount(image) }}
  </div>
</div>

<style scoped>
.character-badge.primary {
  background: #4a90e2;
  color: white;
  font-weight: bold;
  font-size: 14px;
  width: 24px;
  height: 24px;
}

.character-badge.secondary-count {
  background: rgba(150, 150, 150, 0.8);
  color: white;
  font-size: 11px;
  width: 20px;
  height: 20px;
}
</style>
```

**Visual Hierarchy:**
- Primary character shown as larger, colored badge with initial
- Additional characters shown as smaller "+N" count badge
- Hover shows character names
- Clear visual distinction between primary and secondary

### Phase 3: Advanced Multi-Character Features (Future - AI/ML Integration)

**Scope:** Automatic detection and per-character AI features
**Prerequisites:** Phase 2 must be complete (UI and data model for multiple characters)

#### 1. Face Detection Enhancements

**Multi-face detection:**
- Detect all faces in image (not just primary)
- Match detected faces against character reference photos
- Automatically suggest secondary characters based on face matches
- Store face bounding box per character in enhanced junction table

#### 2. Per-Character Tagging

**Character-specific tags:**
- Enhance junction table to store tags per character association
- Example schema: `picture_faces(picture_id, character_id, face_bbox, tags_json)`
- Generate different tags for each character: "Alice: [blonde, formal_dress]", "Bob: [suit, glasses]"
- Filter images by character+tag combinations: "Alice in a red dress"

#### 3. Multi-Character Embeddings

**Embedding context enhancement:**
- Include all character names in embedding generation
- Weight primary character 2x higher than secondary characters
- Example: "Alice (primary) and Bob at the beach with sunset"
- Enable complex searches: "Alice and Bob together", "photos with both Alice and Carol"
- Semantic understanding of character relationships in pictures

#### 4. Character-Aware Search

**Enhanced search capabilities:**
- Search for pictures with specific character combinations
- Support queries like: "Alice alone", "Alice with anyone", "Alice with Bob"
- Character co-occurrence suggestions
- Timeline views showing character interactions

**Note:** Phase 2 provides manual multi-character management. Phase 3 adds AI automation and per-character intelligence. The `primary_character_id` approach works perfectly for the current single-person-per-photo workflow.

## Database Migration Strategy

Since you're okay with recreating the database:

### Option 1: Clean Recreation (Recommended)
1. Delete `vault.db`
2. Delete all `__pycache__` directories
3. Restart server - will create new schema
4. Re-import all images

### Option 2: Manual Migration (If keeping data)
```sql
-- Create new table
CREATE TABLE picture_characters (
    picture_id TEXT NOT NULL,
    character_id INTEGER NOT NULL,
    PRIMARY KEY (picture_id, character_id),
    FOREIGN KEY (picture_id) REFERENCES pictures(id),
    FOREIGN KEY (character_id) REFERENCES characters(id)
);

-- Migrate existing data
INSERT INTO picture_characters (picture_id, character_id)
SELECT id, character_id FROM pictures WHERE character_id IS NOT NULL;

-- Drop old column (SQLite requires table recreation)
-- ... (complex SQLite ALTER TABLE workaround needed)
```

## Testing Checklist

### Phase 1 Tests (✅ Ready to Test Now)
- [ ] Create picture with primary character
- [ ] Create picture with no character (null primary_character_id)
- [ ] Assign primary character to existing picture
- [ ] Change primary character of a picture
- [ ] Delete picture (should work as before)
- [ ] Delete character (should work as before)
- [ ] Filter pictures by primary character
- [ ] Get character counts (using primary_character_id)
- [ ] Search with character filters (uses primary_character_id)
- [ ] Frontend filters by character correctly
- [ ] Character counts display correctly in sidebar
- [ ] Can assign primary character via drag-and-drop
- [ ] Upload new picture with primary character

### Phase 2 Tests (Future - Multi-Character UI)
**Manual character management:**
- [ ] Assign multiple characters to picture via UI
- [ ] Add secondary character to existing picture
- [ ] Remove secondary character from picture (not primary)
- [ ] Change primary character (new primary removed from secondary list)
- [ ] Promote secondary character to primary
- [ ] Display pictures with multiple character badges (primary + count)
- [ ] Filter by character includes both primary and secondary associations
- [ ] Junction table cleanup on picture deletion
- [ ] Junction table cleanup on character deletion
- [ ] Prevent duplicate character assignments (primary vs secondary)
- [ ] Upload new picture with primary + secondary characters

### Phase 3 Tests (Future - AI/ML Features)
**Automatic detection and per-character intelligence:**
- [ ] Multi-face detection identifies all faces in image
- [ ] Face matching suggests secondary characters automatically
- [ ] Per-character tags generated and stored separately
- [ ] Search for character+tag combinations ("Alice in red dress")
- [ ] Multi-character embeddings include all character names
- [ ] Search for character co-occurrence ("Alice and Bob together")
- [ ] Primary character weighted higher in embeddings (2x)
- [ ] Face bounding boxes stored per character in junction table
- [ ] Character-specific tag filtering works correctly
- [ ] Complex multi-character queries return correct results

## Breaking Changes

### API Changes (Phase 1 - Already Applied)
1. **ALL Endpoints:**
   - Parameter renamed: `character_id` → `primary_character_id`
   - Response field renamed: `character_id` → `primary_character_id`
   - **Impact:** Minimal - just a field name change, all logic works the same

### Future API Changes (Phase 2 - Not Breaking)
1. **POST /pictures/upload**
   - Will accept optional `character_ids` array parameter
   - `primary_character_id` continues to work as before (backward compatible)

2. **PATCH /pictures/{id}**
   - Will accept optional `character_ids` array for updates
   - `primary_character_id` updates continue to work (backward compatible)

3. **GET /pictures response:**
   - Will include `character_ids` array field (populated from junction table)
   - `primary_character_id` field remains (backward compatible)

### Database Schema Changes
- `pictures` table: `character_id` → `primary_character_id` (renamed, foreign key maintained)
- New table: `picture_characters` (junction table for many-to-many, not used yet in Phase 1)
- All indexes maintained

## Effort Tracking

### Phase 1: Single Character Support (✅ COMPLETE)
- ✅ Schema changes: 30 min
- ✅ Backend renaming (character_id → primary_character_id): 45 min  
- ✅ Server endpoints update: 30 min
- ✅ Frontend API calls update: 20 min
- ✅ Test updates: 15 min
- ✅ CLI deletion: 5 min
- **Phase 1 Total: ~2.5 hours** ✅

### Phase 2: Multi-Character UI & Basic Junction Table (Future)
When we need manual multi-character assignment:
- Junction table integration in Pictures CRUD: 45 min
- Server endpoint enhancements (character_ids parameter): 30 min
- Character deletion cleanup (junction table): 15 min
- Frontend ImageOverlay UI (primary + secondary sections): 1.5 hours
- Frontend ImageImporter UI (primary + additional dropdowns): 45 min
- Frontend thumbnail badges (primary + count): 30 min
- Backend business rules (prevent duplicates, sync): 30 min
- Testing: 45 min
- **Phase 2 Total: ~5 hours**

### Phase 3: Advanced AI/ML Multi-Character Features (Future)
When we need automatic detection and per-character intelligence:
- Multi-face detection implementation: 2 hours
- Face matching against character references: 1.5 hours
- Junction table enhancement (face_bbox, tags per character): 1 hour
- Per-character tag generation: 1.5 hours
- Multi-character embedding generation: 2 hours
- Character-aware search implementation: 1.5 hours
- Frontend UI for automatic suggestions: 1 hour
- Testing and refinement: 2 hours
- **Phase 3 Total: ~12.5 hours**

**Grand Total: ~20 hours** (all phases)

## Implementation Status

### Phase 1: Single Character (primary_character_id) ✅ COMPLETE
1. ✅ Schema setup - Junction table models created
2. ✅ Database field rename - `character_id` → `primary_character_id`
3. ✅ Backend updates - All files updated to use new field name
4. ✅ Server endpoints - All API routes updated
5. ✅ Frontend updates - All API calls updated
6. ✅ Test updates - All tests passing with new field name
7. ✅ CLI deleted - Removed unused code

**Status:** ✅ Ready for database recreation and testing
**Trigger:** Can use immediately

### Phase 2: Multi-Character UI & Basic Management (Future)
1. ⏸️ Pictures CRUD - Add junction table integration
2. ⏸️ Server endpoints - Add `character_ids` array parameter support
3. ⏸️ Character deletion - Add junction table cleanup
4. ⏸️ ImageOverlay UI - Primary + secondary character sections
5. ⏸️ ImageImporter UI - Primary + additional character dropdowns
6. ⏸️ Thumbnail badges - Primary character badge + count indicator
7. ⏸️ Business rules - Prevent duplicates, sync primary/secondary
8. ⏸️ Testing - Multi-character assignment and display

**Status:** ⏸️ Deferred until manual group photo support is needed
**Trigger:** When user wants to manually tag multiple people in pictures
**Estimate:** ~5 hours

### Phase 3: Advanced AI/ML Multi-Character Features (Future)
1. ⏸️ Multi-face detection - Detect all faces in images
2. ⏸️ Face matching - Match faces to character references
3. ⏸️ Per-character tagging - Generate tags for each detected character
4. ⏸️ Junction table enhancement - Add face_bbox and tags_json fields
5. ⏸️ Multi-character embeddings - Include all characters in context
6. ⏸️ Character-aware search - Complex multi-character queries
7. ⏸️ Automatic suggestions - UI for suggested secondary characters
8. ⏸️ Testing - AI feature validation

**Status:** ⏸️ Deferred until Phase 2 complete and AI features needed
**Trigger:** When automatic multi-character detection becomes valuable
**Prerequisites:** Phase 2 must be complete
**Estimate:** ~12.5 hours

## Key Decisions & Rationale

### Why primary_character_id Works Well
1. **Backward Compatibility:** All existing code works with just a field rename
2. **Simplicity:** No complex join queries needed for everyday filtering
3. **Performance:** Direct column lookup is faster than junction table queries
4. **Gradual Migration:** Can add multi-character support later without disruption
5. **Clear Semantics:** "Primary character" clearly indicates the main subject
6. **UI Clarity:** Makes it easy to distinguish the main subject from background characters
7. **Face Detection:** Aligns with single-face detection (primary face = primary character)
8. **Embeddings:** Character embeddings naturally focus on the primary character

### Why Later Phases Are Deferred

**Phase 2 Deferral Reasons:**
1. **UI Complexity:** Multi-character UI adds significant frontend work
2. **Actual Need:** Most photos have one main subject; group photos are minority use case
3. **Manual Management:** Users can still work around by choosing which character is primary
4. **Foundation Ready:** Junction table exists, just not actively used yet

**Phase 3 Deferral Reasons:**
1. **Phase 2 Prerequisite:** Need UI and data model for multiple characters first
2. **AI Complexity:** Multi-face detection and matching is sophisticated
3. **Training Data:** Need good reference photos per character for face matching
4. **Per-Character Features:** Tags and embeddings per character adds complexity
5. **Diminishing Returns:** Primary character handles 90% of use cases well

### When to Implement Phase 2
Trigger Phase 2 when:
- User explicitly needs to track multiple people per picture
- Group photos become common in the collection
- Manual multi-character assignment becomes a regular workflow
- UI design for character management is finalized

### When to Implement Phase 3
Trigger Phase 3 when:
- Phase 2 is complete and stable
- User has many group photos that need automatic detection
- Face detection is mature and reliable
- Character reference photos are available for matching
- Per-character search becomes a requested feature

### Business Rules for Primary vs Additional Characters

**Phase 2 Implementation Rules:**

1. **Primary Character:**
   - Stored in `pictures.primary_character_id` column (foreign key, indexed)
   - Used for filtering, face detection, embeddings, character tags
   - Can be `NULL` (unassigned pictures)
   - Can be changed at any time via dropdown/assignment
   - Automatically included in the `character_ids` list when present

2. **Additional Characters:**
  - Stored in `picture_faces` junction table
   - Used for multi-character search and display
   - Cannot include the primary character ID (avoid duplication)
   - Can be added/removed independently
   - Don't affect face detection or embeddings

3. **UI Constraints:**
   - Can't add a character as "additional" if they're already the primary
   - Changing primary character should remove old primary from additional list
   - When setting primary character to someone in additional list, remove them from additional
   - Deleting primary character (set to NULL) can optionally promote first additional character

4. **Backend Sync:**
   - When primary_character_id is set, ensure it's NOT in the junction table (remove if present)
   - When retrieving `character_ids`, include primary_character_id + junction table entries
   - When updating via API, handle both `primary_character_id` and `character_ids[]` parameters

### Database Recreation Strategy
Since we're okay with recreating the database:
1. Delete `vault.db`
2. Server creates fresh schema with `primary_character_id` field
3. Junction table exists but unused (ready for Phase 2)
4. Re-import all images with primary characters assigned
