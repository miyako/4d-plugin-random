#include <cassert>
#include <cstdlib>
#include <cstring>
#include <map>
#include <string>
#include <Security/Security.h>

// Compile the actual helper/wrappers and entire unchanged SDK, not substitutes.
#include "4DPluginAPI.c"
#include "4DPlugin-JSON.cpp"
#include "C_TEXT.cpp"
#include "ARRAY_TEXT.cpp"
#include "4DPlugin-random.cpp"

struct Reference {
    size_t owners = 1;
    std::map<CUTF16String, PA_Variable> properties;
};

static bool allocatedEmpty;
static std::map<PA_Unichar*, size_t> strings;
static std::map<PA_Handle, size_t> handles;
static std::map<Reference*, bool> references;
static CUTF16String commandText;
static bool stringifyMismatch;

static PA_Unistring makeString(const PA_Unichar* text, size_t length) {
    PA_Unistring result = {};
    result.fLength = static_cast<PA_long32>(length);
    if (length || allocatedEmpty) {
        result.fString = static_cast<PA_Unichar*>(malloc((length + 1) * sizeof(PA_Unichar)));
        assert(result.fString);
        if (length) memcpy(result.fString, text, length * sizeof(PA_Unichar));
        result.fString[length] = 0;
        PA_Unichar* allocation = result.fString;
        assert(strings.emplace(allocation, length).second);
    }
    return result;
}

static CUTF16String readString(PA_Unistring value) {
    PA_Unichar* allocation = value.fString;
    if (!allocation) {
        assert(value.fLength == 0);
        return {};
    }
    assert(strings.at(allocation) == static_cast<size_t>(value.fLength));
    assert(value.fString[value.fLength] == 0);
    return CUTF16String(value.fString, value.fLength);
}

static void releaseString(PA_Unistring value) {
    PA_Unichar* allocation = value.fString;
    if (allocation) {
        readString(value);
        assert(strings.erase(allocation) == 1);
        free(allocation);
    } else {
        assert(value.fLength == 0);
    }
}

static Reference* newReference() {
    auto* value = new Reference;
    assert(references.emplace(value, true).second);
    return value;
}

static PA_Variable copyVariable(PA_Variable value) {
    if (value.fType == eVK_Unistring) {
        readString(value.uValue.fString);
        value.uValue.fString = makeString(value.uValue.fString.fString,
                                         value.uValue.fString.fLength);
    } else if (value.fType == eVK_Object || value.fType == eVK_Collection) {
        auto* ref = static_cast<Reference*>(value.uValue.fObject);
        if (ref) {
            assert(references.count(ref));
            ++ref->owners;
        }
    }
    return value;
}

static void clearVariable(PA_Variable* value) {
    if (value->fType == eVK_Unistring) {
        releaseString(value->uValue.fString);
    } else if (value->fType == eVK_Object || value->fType == eVK_Collection) {
        auto* ref = static_cast<Reference*>(value->uValue.fObject);
        if (ref) {
            assert(references.count(ref) && ref->owners);
            if (--ref->owners == 0) {
                for (auto& property : ref->properties) clearVariable(&property.second);
                assert(references.erase(ref) == 1);
                delete ref;
            }
        }
    } else if (value->fType == eVK_ArrayUnicode) {
        PA_Handle h = value->uValue.fArray.fData;
        if (h) {
            assert(handles.at(h) == (value->uValue.fArray.fNbElements + 1) * sizeof(PA_Unistring));
            auto* elements = reinterpret_cast<PA_Unistring*>(*h);
            for (PA_long32 i = 0; i <= value->uValue.fArray.fNbElements; ++i)
                releaseString(elements[i]);
            assert(handles.erase(h) == 1);
            free(*h);
            free(h);
        }
    }
    *value = {};
    value->fType = eVK_Undefined;
}

static void host(short selector, EngineBlock* eb) {
    switch (selector) {
        case EX_CREATE_UNISTRING: {
            auto* text = reinterpret_cast<PA_Unichar*>(eb->fHandle);
            size_t length = 0;
            if (text) while (text[length]) ++length;
            eb->fUniString1 = makeString(text, length);
            break;
        }
        case EX_DISPOSE_UNISTRING:
            releaseString(eb->fUniString1);
            break;
        case EX_SET_UNISTRING: {
            auto* text = reinterpret_cast<PA_Unichar*>(eb->fHandle);
            size_t length = 0;
            if (text) while (text[length]) ++length;
            PA_Unistring replacement = makeString(text, length);
            releaseString(eb->fUniString1);
            eb->fUniString1 = replacement;
            break;
        }
        case EX_VARIABLE_TO_STRING: {
            auto* value = reinterpret_cast<PA_Variable*>(eb->fHandle);
            assert(value->fType == eVK_Unistring);
            readString(value->uValue.fString);
            eb->fUniString1 = value->uValue.fString;
            break;
        }
        case EX_CLEAR_VARIABLE:
            clearVariable(static_cast<PA_Variable*>(eb->fPtr1));
            break;
        case EX_GET_INFORMATION:
            eb->fParam2 = 0x00001700;
            break;
        case EX_CONVERT_STRING: {
            assert(eb->fParam1 == 5 && eb->fParam2 == 0);
            PA_Handle h = PA_NewHandle(static_cast<PA_long32>(eb->fParam4));
            if (eb->fParam4)
                memcpy(*h, reinterpret_cast<void*>(eb->fParam3), eb->fParam4);
            eb->fHandle = h;
            break;
        }
        case EX_GET_OBJ_VALUE:
        case EX_SET_OBJ_VALUE: {
            assert(eb->fShort == eVK_Object);
            auto* ref = static_cast<Reference*>(eb->fPtr1);
            assert(references.count(ref));
            CUTF16String key = readString(eb->fUniString1);
            auto* value = static_cast<PA_Variable*>(eb->fPtr2);
            if (selector == EX_GET_OBJ_VALUE) {
                *value = copyVariable(ref->properties.at(key));
            } else {
                PA_Variable copy = copyVariable(*value);
                auto found = ref->properties.find(key);
                if (found != ref->properties.end()) clearVariable(&found->second);
                ref->properties[key] = copy;
            }
            break;
        }
        case EX_HANDLE_MANAGER: {
            PA_Handle h = eb->fHandle;
            switch (eb->fParam1) {
                case 1:
                    h = static_cast<PA_Handle>(malloc(sizeof(*h)));
                    assert(h);
                    *h = static_cast<char*>(calloc(1, eb->fParam2 ? eb->fParam2 : 1));
                    assert(*h);
                    handles[h] = eb->fParam2;
                    eb->fHandle = h;
                    break;
                case 2:
                    assert(handles.erase(h) == 1);
                    free(*h);
                    free(h);
                    break;
                case 3:
                    assert(handles.count(h));
                    *h = static_cast<char*>(realloc(*h, eb->fParam2 ? eb->fParam2 : 1));
                    assert(*h);
                    handles[h] = eb->fParam2;
                    break;
                case 4:
                    eb->fParam2 = handles.at(h);
                    break;
                case 5:
                    assert(handles.count(h));
                    eb->fParam3 = reinterpret_cast<sLONG_PTR>(*h);
                    break;
                case 6:
                    assert(handles.count(h));
                    break;
                default: assert(false);
            }
            break;
        }
        case EX_EXECUTE_COMMAND_BY_ID: {
            auto** args = static_cast<PA_Variable**>(eb->fPtr1);
            auto* result = static_cast<PA_Variable*>(eb->fPtr2);
            if (eb->fParam1 == 1471) {
                assert(eb->fParam2 == 0);
                PA_SetObjectVariable(result, newReference());
            } else if (eb->fParam1 == 1231) {
                assert(eb->fParam2 == 2);
                auto* ref = static_cast<Reference*>(PA_GetObjectVariable(*args[0]));
                PA_SetBooleanVariable(result, ref->properties.count(readString(args[1]->uValue.fString)));
            } else if (eb->fParam1 == 1225) {
                auto* input = static_cast<Reference*>(PA_GetObjectVariable(*args[0]));
                auto* output = newReference();
                for (const auto& pair : input->properties)
                    output->properties[pair.first] = copyVariable(pair.second);
                PA_SetObjectVariable(result, output);
            } else if (eb->fParam1 == 1217) {
                assert(references.count(static_cast<Reference*>(PA_GetObjectVariable(*args[0]))));
                if (stringifyMismatch) {
                    PA_SetObjectVariable(result, newReference());
                } else {
                    PA_Unistring text = makeString(commandText.data(), commandText.size());
                    PA_SetStringVariable(result, &text);
                }
            } else {
                assert(false);
            }
            break;
        }
        default:
            fprintf(stderr, "Unexpected engine selector %d\n", selector);
            abort();
    }
    eb->fError = eER_NoErr;
}

static CUTF16String utf16(const char* text) {
    C_TEXT converted;
    converted.setUTF8String(reinterpret_cast<const uint8_t*>(text), strlen(text));
    CUTF16String result;
    converted.copyUTF16String(&result);
    return result;
}

static void expectClean() {
    if (!strings.empty() || !handles.empty() || !references.empty()) {
        fprintf(stderr, "Ownership imbalance: %zu strings, %zu handles, %zu references\n",
                strings.size(), handles.size(), references.size());
        abort();
    }
}

static void testSetters(int overload) {
    const char* fixtures[] = {"", "ASCII", "\xE6\x97\xA5\xE6\x9C\xAC", "A\xF0\x9F\x98\x80Z"};
    const wchar_t* wide[] = {L"", L"ASCII", L"\u65e5\u672c", L"A\U0001f600Z"};
    const char16_t* units[] = {u"", u"ASCII", u"\u65e5\u672c", u"A\U0001f600Z"};
    const size_t lengths[] = {0, 5, 2, 4};
    for (size_t i = 0; i < 4; ++i) {
        CUTF16String expected(reinterpret_cast<const PA_Unichar*>(units[i]), lengths[i]);
        assert(utf16(fixtures[i]) == expected);
        assert(expected.size() == lengths[i]);
        {
            auto* obj = newReference();
            for (int repeat = 0; repeat < 3; ++repeat) {
                switch (overload) {
                    case 0: ob_set_s(obj, "text", fixtures[i]); break;
                    case 1: ob_set_s(obj, L"text", fixtures[i]); break;
                    case 2: ob_set_a(obj, L"text", wide[i]); break;
                    case 3: ob_set_a(obj, L"text", &expected); break;
                }
                assert(readString(obj->properties.at(utf16("text")).uValue.fString) == expected);
            }
            PA_DisposeObject(obj);
            expectClean();
        }
    }
}

static void testGetters(int getter) {
    CUTF16String keyText = utf16("text");
    const char* fixtures[] = {"", "ASCII", "\xE6\x97\xA5\xE6\x9C\xAC", "A\xF0\x9F\x98\x80Z"};
    const char16_t* units[] = {u"", u"ASCII", u"\u65e5\u672c", u"A\U0001f600Z"};
    const size_t lengths[] = {0, 5, 2, 4};
    for (size_t i = 0; i < 4; ++i) {
        const char* text = fixtures[i];
        auto* obj = newReference();
        CUTF16String expected(reinterpret_cast<const PA_Unichar*>(units[i]), lengths[i]);
        PA_Variable value = {};
        value.fType = eVK_Unistring;
        value.uValue.fString = makeString(expected.data(), expected.size());
        obj->properties[keyText] = value;
        for (int repeat = 0; repeat < 3; ++repeat) {
            size_t before = strings.size();
            CUTF8String output;
            if (getter == 0) {
                assert(ob_get_a(obj, L"text", &output));
                assert(output == CUTF8String(reinterpret_cast<const uint8_t*>(text)));
            } else if (getter == 1) {
                assert(!ob_get_b(obj, L"text"));
            } else if (getter == 2) {
                assert(ob_get_n(obj, L"text") == 0);
            } else {
                assert(ob_get_c(obj, L"text") == nullptr);
            }
            assert(strings.size() == before && "property result was not cleared");
            assert(readString(obj->properties.at(keyText).uValue.fString) == expected);
        }
        PA_DisposeObject(obj);
        expectClean();
    }
    for (int kind : {eVK_Object, eVK_Collection, eVK_Boolean, eVK_Real}) {
        auto* obj = newReference();
        PA_Variable value = {};
        if (kind == eVK_Object) PA_SetObjectVariable(&value, newReference());
        if (kind == eVK_Collection) PA_SetCollectionVariable(&value, newReference());
        if (kind == eVK_Boolean) PA_SetBooleanVariable(&value, true);
        if (kind == eVK_Real) PA_SetRealVariable(&value, 42.5);
        obj->properties[keyText] = value;
        CUTF8String output(reinterpret_cast<const uint8_t*>("unchanged"));
        assert(ob_get_a(obj, L"text", &output));
        assert(output == CUTF8String(reinterpret_cast<const uint8_t*>("unchanged")));
        assert(ob_get_b(obj, L"text") == (kind == eVK_Boolean));
        assert(ob_get_n(obj, L"text") == (kind == eVK_Real ? 42.5 : 0));
        PA_CollectionRef returned = ob_get_c(obj, L"text");
        if (kind == eVK_Collection) {
            auto* ref = static_cast<Reference*>(returned);
            assert(ref == value.uValue.fCollection && ref->owners == 2);
            PA_DisposeObject(obj);
            assert(references.count(ref) && ref->owners == 1);
            PA_DisposeCollection(returned);
        } else {
            assert(!returned);
            if (kind == eVK_Object)
                assert(static_cast<Reference*>(value.uValue.fObject)->owners == 1);
            PA_DisposeObject(obj);
        }
        expectClean();
    }
    CUTF8String unchanged(reinterpret_cast<const uint8_t*>("unchanged"));
    auto* empty = newReference();
    assert(!ob_get_a(empty, L"missing", &unchanged));
    assert(!ob_get_b(empty, L"missing") && ob_get_n(empty, L"missing") == 0);
    assert(!ob_get_c(empty, L"missing"));
    PA_DisposeObject(empty);
    assert(!ob_get_a(nullptr, L"missing", &unchanged));
    assert(unchanged == CUTF8String(reinterpret_cast<const uint8_t*>("unchanged")));
    expectClean();
}

static void testArrays() {
    PA_Variable input = PA_CreateVariable(eVK_ArrayUnicode);
    PA_ResizeArray(&input, 2);
    CUTF16String expected = utf16("A\xF0\x9F\x98\x80Z");
    PA_Unistring first = makeString(expected.data(), expected.size());
    PA_Unichar* allocation = first.fString;
    PA_SetStringInArray(input, 1, &first);
    assert(PA_GetStringInArray(input, 1).fString == allocation);
    ARRAY_TEXT wrapper;
    BytePtr params[] = {reinterpret_cast<BytePtr>(&input)};
    wrapper.fromParamAtIndex(params, 1);
    PA_ResizeArray(&input, 0);
    PA_ClearVariable(&input);
    CUTF16String copied;
    wrapper.copyUTF16StringAtIndex(&copied, 1);
    assert(copied == expected);
    PA_Variable output = PA_CreateVariable(eVK_Undefined);
    BytePtr outputs[] = {reinterpret_cast<BytePtr>(&output)};
    wrapper.toParamAtIndex(outputs, 1);
    PA_Unistring borrowed = PA_GetStringInArray(output, 1);
    assert(readString(borrowed) == expected);
    PA_Variable independent = PA_CreateVariable(eVK_Undefined);
    BytePtr independentParams[] = {reinterpret_cast<BytePtr>(&independent)};
    wrapper.toParamAtIndex(independentParams, 1);
    assert(PA_GetStringInArray(independent, 1).fString != borrowed.fString);
    wrapper.setUTF16StringAtIndex(reinterpret_cast<const PA_Unichar*>(u"new"), 3, 1);
    wrapper.toParamAtIndex(outputs, 1);
    assert(readString(PA_GetStringInArray(output, 1)) == utf16("new"));
    PA_ClearVariable(&output);
    assert(readString(PA_GetStringInArray(independent, 1)) == expected);
    PA_ClearVariable(&independent);
    for (int rejection = 0; rejection < 4; ++rejection) {
        PA_Variable array = PA_CreateVariable(eVK_ArrayUnicode);
        PA_Unistring owned = makeString(expected.data(), expected.size());
        PA_Variable destination = array;
        if (rejection == 0) destination.fType = eVK_Real;
        if (rejection == 1) destination.uValue.fArray.fData = nullptr;
        PA_SetStringInArray(destination, rejection == 2 ? -1 : rejection == 3 ? 1 : 0, &owned);
        assert(readString(owned) == expected);
        PA_DisposeUnistring(&owned);
        PA_SetStringInArray(array, 0, nullptr);
        PA_ClearVariable(&array);
    }
    expectClean();
}

static void testStringify() {
    auto* obj = newReference();
    CUTF16String key = utf16("input");
    PA_Variable input = {};
    PA_Unistring text = makeString(reinterpret_cast<const PA_Unichar*>(u"independent"), 11);
    PA_SetStringVariable(&input, &text);
    obj->properties[key] = input;
    for (const char* text : {"", "ASCII", "\xE6\x97\xA5", "\xF0\x9F\x98\x80"}) {
        commandText = utf16(text);
        CUTF8String result;
        ob_stringify(obj, &result);
        assert(result == CUTF8String(reinterpret_cast<const uint8_t*>(text)));
        assert(obj->owners == 1 && references.size() == 1 && strings.size() == 1);
        assert(readString(obj->properties.at(key).uValue.fString) == utf16("independent"));
    }
    stringifyMismatch = true;
    CUTF8String result(reinterpret_cast<const uint8_t*>("unchanged"));
    ob_stringify(obj, &result);
    assert(result == CUTF8String(reinterpret_cast<const uint8_t*>("unchanged")));
    assert(references.size() == 1);
    PA_DisposeObject(obj);
    expectClean();
}

static void testScalarCopies() {
    const PA_Unichar* units = reinterpret_cast<const PA_Unichar*>(u"A\U0001f600Z");
    CUTF16String expected(units, 4);
    PA_Unistring input = makeString(units, 4);
    BytePtr args[] = {reinterpret_cast<BytePtr>(&input)};
    C_TEXT wrapper;
    wrapper.fromParamAtIndex(args, 1);
    PA_SetUnistring(&input, const_cast<PA_Unichar*>(reinterpret_cast<const PA_Unichar*>(u"changed")));
    CUTF16String copied;
    wrapper.copyUTF16String(&copied);
    assert(copied == expected);
    wrapper.toParamAtIndex(args, 1);
    assert(readString(input) == expected);
    assert(input.fString != wrapper.getUTF16StringPtr());
    wrapper.toParamAtIndex(args, 1);
    PA_DisposeUnistring(&input);
    auto* obj = newReference();
    auto* collection = newReference();
    ob_set_c(obj, L"collection", collection);
    assert(references.count(collection) && collection->owners == 1);
    auto* child = newReference();
    ob_set_o(obj, L"object", child);
    assert(references.count(child) && child->owners == 1);
    auto* narrowChild = newReference();
    ob_set_o(obj, "narrow", narrowChild);
    assert(references.count(narrowChild) && narrowChild->owners == 1);
    PA_CollectionRef returned = ob_get_c(obj, L"collection");
    PA_DisposeObject(obj);
    assert(returned == collection && collection->owners == 1);
    PA_DisposeCollection(returned);
    expectClean();
}

static void testCommands() {
    auto* obj = newReference();
    CUTF16String key = utf16("size");
    PA_Variable size = {};
    PA_Unistring wrongType = makeString(reinterpret_cast<const PA_Unichar*>(u"wrong"), 5);
    PA_SetStringVariable(&size, &wrongType);
    obj->properties[key] = size;
    PA_ObjectRef argument = obj;
    void* args[] = {&argument};
    PA_Handle result = nullptr;
    PluginBlock parameters = {};
    parameters.fParameters = args;
    parameters.fResult = &result;
    size_t before = strings.size();
    PluginMain(2, &parameters);
    assert(result && PA_GetHandleSize(result) == 0);
    assert(strings.size() == before && "command-reachable mismatch leaked");
    PA_DisposeHandle(result);
    clearVariable(&obj->properties.at(key));
    PA_SetRealVariable(&obj->properties.at(key), 4);
    PluginMain(2, &parameters);
    assert(result && PA_GetHandleSize(result) == 4);
    PA_DisposeHandle(result);
    PA_DisposeObject(obj);
    PA_ObjectRef returned = nullptr;
    parameters.fResult = &returned;
    PluginMain(1, &parameters);
    assert(returned && ob_get_b(returned, L"success"));
    double number = ob_get_n(returned, L"value");
    assert(number >= -2147483648.0 && number <= 2147483647.0);
    PA_DisposeObject(returned);
    expectClean();
}

int main(int argc, char** argv) {
    assert(argc == 3);
    gCall4D = host;
    allocatedEmpty = std::string(argv[2]) == "allocated";
    const std::string test = argv[1];
    if (test.compare(0, 6, "setter") == 0) testSetters(test.at(6) - '0');
    else if (test.compare(0, 6, "getter") == 0) testGetters(test.at(6) - '0');
    else if (test == "arrays") testArrays();
    else if (test == "stringify") testStringify();
    else if (test == "commands") testCommands();
    else if (test == "scalar") testScalarCopies();
    else assert(false);
    expectClean();
    puts("ownership host passed");
}
